#!/usr/bin/env python3
import os
import yaml
import click
import logging
import boto3
import pprint
from terminaltables import AsciiTable
from easysettings import JSONSettings as Settings
from prompt_toolkit import prompt
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.validation import Validator, ValidationError
from colored import fg, attr
import humanize
import datetime
import itertools

settings = Settings()

settings_file = ".settings.conf"
if not os.path.exists(settings_file):
    settings.save(settings_file)
else:
    settings.load(settings_file)



class ChoicesValidator(Validator):
    def __init__(self, choices):
        self.choices = choices

    def validate(self, document):
        text = document.text

        if text not in self.choices:
            raise ValidationError(message='Use Arrow down key to select from the list', cursor_position=0)


class ChoicesCompleter(Completer):
    def __init__(self, choices):
        self.choices = choices

    def get_completions(self, document, complete_event):
        for c in self.choices:
            if c.lower().startswith(document.text.lower()):
                yield Completion(c, start_position=-len(document.text))


def chunks(iterable,size):
    it = iter(iterable)
    chunk = tuple(itertools.islice(it,size))
    while chunk:
        yield chunk
        chunk = tuple(itertools.islice(it,size))

# ugly
reset = attr('reset')

log = logging.getLogger(__name__)


def dump(data):
    pprint.pprint(data)


ecs = boto3.client('ecs')
ec2 = boto3.client('ec2')
elb = boto3.client('elbv2')
logs = boto3.client('logs')



def get_cluster_ids(ecs):
    clusters = ecs.list_clusters()['clusterArns']
    return [c.split(':cluster/')[1] for c in clusters]


def get_clusters_info(cluster_ids):
    return ecs.describe_clusters(clusters=cluster_ids)['clusters']

def print_table(header, data):
    print("")
    print(AsciiTable([header] + data).table)
    print("")


def print_clusters_info(clusters_info):
    header = ['Region', 'Cluster', 'Container Instances', 'Running Tasks', 'Active Services']

    data = [[
        c['clusterArn'].split("arn:aws:ecs:")[1].split(":")[0],
        c['clusterName'],
        c['registeredContainerInstancesCount'],
        c['runningTasksCount'],
        c['activeServicesCount'],
    ] for c in clusters_info]

    print_table(header, data)

def print_msg_success(msg):
    print(fg('green') + "\n\t" + msg + reset)


def get_default_cluster():
    if not settings.get('cluster'):
        cluster_ids = get_cluster_ids(ecs)
        clusters_info = get_clusters_info(cluster_ids)

        print_msg_success("No default cluster set, please pick one: ")

        print_clusters_info(clusters_info)

        cluster_names = [c['clusterName'] for c in clusters_info]

        cluster = prompt('Cluster: ', validator=ChoicesValidator(choices=cluster_names),
                      completer=ChoicesCompleter(choices=cluster_names))

        settings.setsave('cluster', cluster)

    cluster = settings.get('cluster')

    print_msg_success("Cluster: {}".format(cluster))

    return cluster

def format_instances(reservations):

    results = []

    for r in reservations:
        for i in r['Instances']:
            name = [t for t in i.get('Tags', []) if t['Key'] == 'Name']
            name = name[0]['Value'] if name else None

            results.append({
                'PrivateIpAddress': i['PrivateIpAddress'],
                'Name': name,
                'ImageId': i['ImageId'],
                'InstanceType': i['InstanceType'],
                'InstanceId': i['InstanceId'],
                'State': i['State']['Name'],
                'AvailabilityZone': i['Placement']['AvailabilityZone'],
                'LaunchTime': i['LaunchTime']
            })

    return results

def get_ec2_instances_by_ids(ids):
    reservations = ec2.describe_instances(InstanceIds=ids)['Reservations']
    return format_instances(reservations)


def get_ec2_instances():
    reservations = ec2.describe_instances(Filters=[{'Name': 'instance-state-name', 'Values': ['running']}])[
        'Reservations']

    return format_instances(reservations)


def print_ec2_instances(instances):
    header = ['Name', 'AvailabilityZone', 'PrivateIpAddress', 'ImageId', 'InstanceType', 'InstanceId', 'State', 'Age']

    data = [[
        i['Name'],
        i['AvailabilityZone'],
        i['PrivateIpAddress'],
        i['ImageId'],
        i['InstanceType'],
        i['InstanceId'],
        i['State'],
        humanize.naturaltime(datetime.datetime.now(datetime.timezone.utc) - i['LaunchTime']),
    ] for i in instances]

    print_table(header, data)

def get_container_instances_by_ids(ids, cluster, include_ec2_instance_detail=True):

    if not ids:
        return []

    instances = ecs.describe_container_instances(containerInstances=ids, cluster=cluster)[
        'containerInstances']

    # flatten results for tabular display
    results = []

    for i in instances:
        attributes = {a['name']: a.get('value', '') for a in i['attributes']}

        registered_resources = {a['name']: a for a in i['registeredResources']}
        remaining_resources = {a['name']: a for a in i['remainingResources']}

        results.append({
            'cluster': cluster,
            'ec2InstanceId': i['ec2InstanceId'],
            'runningTasksCount': i['runningTasksCount'],
            'pendingTasksCount': i['pendingTasksCount'],
            'containerInstanceArn': i['containerInstanceArn'],
            'agentConnected': i['agentConnected'],
            'status': i['status'],
            'dockerVersion': i['versionInfo']['dockerVersion'],
            'ecs.ami-id': attributes.get('ecs.ami-id', ''),
            'ecs.instance-type': attributes.get('ecs.instance-type', ''),
            'ecs.availability-zone': attributes.get('ecs.availability-zone', ''),
            'registered.CPU': registered_resources.get('CPU', {}).get('integerValue'),
            'registered.MEMORY': registered_resources.get('MEMORY', {}).get('integerValue'),
            'remaining.CPU': remaining_resources.get('CPU', {}).get('integerValue'),
            'remaining.MEMORY': remaining_resources.get('MEMORY', {}).get('integerValue'),

        })

    if include_ec2_instance_detail:
        ec2_instance_ids = [i['ec2InstanceId'] for i in results]

        instances = {x['InstanceId'] : x for x in get_ec2_instances_by_ids(ec2_instance_ids)}

        for x in results:
            x['ec2Detail'] = instances[x['ec2InstanceId']]

    return results


def get_container_instances_by_cluster_name(cluster, include_ec2_instance_detail=True):

    instances_ids = ecs.list_container_instances(cluster=cluster)['containerInstanceArns']

    results = get_container_instances_by_ids(instances_ids, cluster, include_ec2_instance_detail=include_ec2_instance_detail)

    return results


def print_container_instances(instances):
    header = ['Cluster', 'ContainerInstance', 'Ec2InstanceId', 'Name', 'Private IP', 'State', 'AmiId', 'Type', 'Zone', 'Status', 'Tasks', 'Pending', 'CPU', 'Mem']

    data = [[
        i['cluster'],
        i['containerInstanceArn'].split(':container-instance/')[1],
        i['ec2InstanceId'],
        i['ec2Detail']['Name'],
        i['ec2Detail']['PrivateIpAddress'],
        i['ec2Detail']['State'],
        i['ecs.ami-id'],
        i['ecs.instance-type'],
        i['ecs.availability-zone'],
        i['status'],
        i['runningTasksCount'],
        i['pendingTasksCount'],
        "{}/{}".format((i['registered.CPU'] - i['remaining.CPU']), i['registered.CPU']),
        "{}/{}".format((i['registered.MEMORY'] - i['remaining.MEMORY']), i['registered.MEMORY']),
    ] for i in instances]

    print_table(header, data)


def get_services_by_cluster_name(cluster):

    service_ids = ecs.list_services(cluster=cluster, maxResults=100)['serviceArns']
    service_ids = [s.split(':service/')[-1] for s in service_ids]
    if not service_ids:
        return []

    results = []

    for c in chunks(service_ids, 10):
        results += ecs.describe_services(services=c, cluster=cluster)['services']

    return results

def get_service_by_name(service, cluster):

    return ecs.describe_services(services=[service], cluster=cluster)['services'][0]

def get_task_definitions(family_prefix):
    return ecs.list_task_definitions(familyPrefix=family_prefix, status="ACTIVE", sort="DESC")['taskDefinitionArns']


def print_task_events(events, max_rows=10):
    header = ['Age', 'Message']

    def format_msg(msg):
        if len(msg)> 100:
            return msg[0:100] + "..."
        return msg

    data = [[
        humanize.naturaltime(datetime.datetime.now(datetime.timezone.utc) - e['createdAt']),
        format_msg(e['message']),
    ] for e in events][0:max_rows]

    print_table(header, data)

def print_tasks(tasks):

    header = ['Group', 'Task', 'TaskDef', 'Ports', 'Name', 'IP', 'Zone', 'Instance', 'Connectivity', 'connectivityAt', 'memory', 'Desired', 'Health', 'Status']

    def format_container_tasks(containers):

        result = []
        for c in containers:
            result.append("{}".format(c['taskArn'].split(":task/")[1]))

        return " ".join(result)

    def format_container_ports(containers):

        result = []
        for c in containers:
            for p in c.get('networkBindings', []):
                result.append("{}->{}".format(p['containerPort'], p['hostPort']))

        return " ".join(result)

    #pprint.pprint(tasks[0])
    #raise

    data = [[
        t['group'],
        format_container_tasks(t['containers']),
        t['taskDefinitionArn'].split(':task-definition/')[1],
        format_container_ports(t['containers']),
        t['container_instance']['ec2Detail']['Name'],
        t['container_instance']['ec2Detail']['PrivateIpAddress'],
        t['container_instance']['ecs.availability-zone'],
        t['container_instance']['ecs.instance-type'],
        t.get('connectivity', ''),
        humanize.naturaltime(datetime.datetime.now(datetime.timezone.utc) - t['connectivityAt']) if 'connectivityAt' in t else "",
        t['memory'],
        t['desiredStatus'],
        t['healthStatus'],
        t['lastStatus'],
    ] for t in tasks]

    print_table(header, data)


def print_services(services):

    header = ['Service Name', 'Task Def', 'Launch Type', 'Desired', 'Running', 'Pending', 'Status', 'Created', 'Deployments (des/pend/run']

    def format_deployments(deployments):
        result = []
        for d in deployments:
            updated_at = humanize.naturaltime(datetime.datetime.now(datetime.timezone.utc) - d['updatedAt'])
            result.append("{}/{}/{} {}".format(d['desiredCount'], d['pendingCount'], d['runningCount'], updated_at))
        msg =  " ".join(result)
        if len(msg)> 80:
            return msg[0:80] + "..."
        return msg

    data = [[
        s['serviceName'],
        s['taskDefinition'].split(":task-definition/")[-1],
        s['launchType'],
        s['desiredCount'],
        s['runningCount'],
        s['pendingCount'],
        s['status'],
        humanize.naturaltime(datetime.datetime.now(datetime.timezone.utc) - s['createdAt']),
        format_deployments(s['deployments']),
    ] for s in services]

    print_table(header, data)


def print_task_def_list(task_def_ids):
    header = ['Task Def']

    data = [[
       td.split(':task-definition/')[1]
    ] for td in task_def_ids[0:5]]

    print_table(header, data)



def delete_service(cluster, service_name):
    result = ecs.delete_service(service=service_name, cluster=cluster)

    status = result['ResponseMetadata']['HTTPStatusCode']
    if status != 200:
        raise Exception("Something went wrong: status={}".format(status))

    return True


def create_service(cluster, service_name, task_definition=None, desired_count=None):
    params = {}

    if desired_count is not None:
        params['desiredCount'] = desired_count

    params['taskDefinition'] = task_definition

    result = ecs.create_service(serviceName=service_name, cluster=cluster, **params)

    status = result['ResponseMetadata']['HTTPStatusCode']
    if status != 200:
        raise Exception("Something went wrong: status={}".format(status))

    return True


def update_service(cluster, service_name, task_definition=None, force_new_deployment=None, desired_count=None):
    params = {}

    if force_new_deployment:
        params['forceNewDeployment'] = True

    if desired_count is not None:
        params['desiredCount'] = desired_count

    if task_definition is not None:
        params['taskDefinition'] = task_definition

    result = ecs.update_service(service=service_name, cluster=cluster, **params)

    status = result['ResponseMetadata']['HTTPStatusCode']
    if status != 200:
        raise Exception("Something went wrong: status={}".format(status))

    return True


def get_task_ids_by_family_and_cluster(family, cluster):

    return ecs.list_tasks(family=family, cluster=cluster)['taskArns']

def get_tasks_by_ids_and_cluster(ids, cluster):
    return ecs.describe_tasks(tasks=ids, cluster=cluster)['tasks']

@click.group()
def main():
    pass


@main.command(name='clusters')
def cmd_clusters():
    """Display Clusters Info"""
    cluster_ids = get_cluster_ids(ecs)
    clusters_info = get_clusters_info(cluster_ids)

    print_clusters_info(clusters_info)

@main.command(name='switch-cluster')
def cmd_switch_cluster():
    """Switch Default Cluster"""
    settings.setsave('cluster', None)

    get_default_cluster()


@main.command(name='list')
def cmd_list_services():
    """List Services"""

    cluster = get_default_cluster()

    services = get_services_by_cluster_name(cluster)
    print_services(services)

@main.command(name='tasks')
@click.argument('service')
def cmd_list_tasks(service):
    """List Tasks for Service """

    cluster = get_default_cluster()

    service_info = get_service_by_name(service, cluster)

    print_services([service_info])

    task_def_list = get_task_definitions(family_prefix=service)

    print_task_def_list(task_def_list)

    events = service_info['events']

    print_task_events(events)

    #pprint.pprint(service_info)

    task_ids = get_task_ids_by_family_and_cluster(family=service, cluster=cluster)

    if task_ids:
        tasks = get_tasks_by_ids_and_cluster(task_ids, cluster)

        container_instance_ids = [t['containerInstanceArn'] for t in tasks]

        results = get_container_instances_by_ids(ids=container_instance_ids, cluster=cluster)

        container_instances_dict = {c['containerInstanceArn']:c for c in results}

        for t in tasks:
            t['container_instance'] = container_instances_dict[t['containerInstanceArn']]

        print_tasks(tasks)


@main.command(name='drain-container-instance')
@click.argument('name')
def cmd_drain_container_instances(name):
    """Set a container Instance to DRAIN"""

    cluster = get_default_cluster()

    # todo: get only the instance we require
    instances = get_container_instances_by_cluster_name(cluster)

    instance = [i for i in instances if i['ec2InstanceId'] == name]

    if not instance:
        print("Not found!")
        return

    instance = instance[0]

    print(fg('green') + "\n\tSetting {} ({}) to DRAIN".format(instance['ec2Detail']['Name'], name) + reset)

    container_instance_arn = instance['containerInstanceArn']

    result = ecs.update_container_instances_state(cluster=cluster, containerInstances=[container_instance_arn], status='DRAINING')

    if result['failures']:
        pprint.pprint(result['failures'])
        raise


@main.command(name='container-instances')
def cmd_container_instances():
    """List container Instances"""

    cluster = get_default_cluster()

    instances = get_container_instances_by_cluster_name(cluster)

    print_container_instances(instances)


@main.command(name='ec2-instances')
def cmd_ec2_instances():
    """List ec2 Instances"""

    instances = get_ec2_instances()

    print_ec2_instances(instances)



@main.command(name='start-task')
@click.argument('taskdefinition')
@click.argument('containerInstance')
def cmd_start_task(taskdefinition, containerinstance):
    """Start task"""

    cluster = get_default_cluster()

    result = ecs.start_task(cluster=cluster, taskDefinition=taskdefinition, containerInstances=[containerinstance])

    if result['ResponseMetadata']['HTTPStatusCode'] != 200:
        print(result['ResponseMetadata'])
        return

    if result['failures']:
        print(result['failures'])
        return

    task = result['tasks'][0]

    print(fg('green') + "\n\tStarted {} task {}".format(taskdefinition, task['taskArn'].split(':task/')[1]) + reset)

@main.command(name='stop-task')
@click.argument('task')
def cmd_stop_task(task):
    """Stop task"""

    cluster = get_default_cluster()

    result = ecs.stop_task(cluster=cluster, task=task)

    if result['ResponseMetadata']['HTTPStatusCode'] != 200:
        print(result['ResponseMetadata'])
        return

    print(fg('green') + "\n\tStoped task {}".format(task) + reset)

def get_default_region():
    return boto3.session.Session().region_name

def get_container_defs_from_file(file_path, cluster_name):

    with open(file_path, 'r') as f:
        service_def = yaml.load(f)

    containerDefinitions =  service_def['TaskDefinition']['Properties']['ContainerDefinitions']

    def lowerCaseFirstLetter(str):
        return str[0].lower() + str[1:]

    def change_keys(obj, convert):
        """
        Recursively goes through the dictionary obj and replaces keys with the convert function.
        """
        if isinstance(obj, (str, int, float)):
            return obj
        if isinstance(obj, dict):
            new = obj.__class__()
            for k, v in obj.items():
                new[convert(k)] = change_keys(v, convert)
        elif isinstance(obj, (list, set, tuple)):
            new = obj.__class__(change_keys(v, convert) for v in obj)
        else:
            return obj
        return new

    containerDefinitions = [change_keys(c, convert=lowerCaseFirstLetter) for c in containerDefinitions]

    region = get_default_region()

    logConfig = {
        'logDriver' : 'awslogs',
        'options' : {
            'awslogs-group': "{}-services".format(cluster_name),
            'awslogs-stream-prefix': containerDefinitions[0]['name'],
            'awslogs-region': region
        }
    }

    containerDefinitions[0]['logConfiguration'] = logConfig

    #pprint.pprint(containerDefinitions)
    return containerDefinitions

def create_log_group(name):
    result = logs.describe_log_groups(logGroupNamePrefix=name)['logGroups']

    for lg in result:
        if lg['logGroupName'] == name:
            return

    logs.create_log_group(logGroupName=name)
    print(fg('green') + "\n\tCreated log group {}".format(name) + reset)


def register_task_def(family, containerDefinitions):

    # Ensure log group exists (or will fail and be hard to diagnose)
    log_groups = list(set([c['logConfiguration']['options']['awslogs-group'] for c in containerDefinitions
                           if c.get('logConfiguration', {}).get('logDriver') == 'awslogs']))

    for lg in log_groups:
        create_log_group(lg)

    result = ecs.register_task_definition(family=family, containerDefinitions=containerDefinitions)

    if result['ResponseMetadata']['HTTPStatusCode'] != 200:
        print(result['ResponseMetadata'])
        raise Exception()

    return result['taskDefinition']['revision']


@main.command(name='register')
@click.argument('name')
def cmd_register(name):
    """Register task definition"""

    cluster = get_default_cluster()

    containerDefinitions = get_container_defs_from_file("./services/{}.yaml".format(name), cluster)

    revision = register_task_def(family=name, containerDefinitions=containerDefinitions)

    print(fg('green') + "\n\t{} now at revision {}".format(name, revision) + reset)


@main.command(name='scale')
@click.argument('name')
@click.argument('desired', type=click.IntRange(0, 16))
def cmd_scale(name, desired):
    """Scale Service"""

    cluster = get_default_cluster()

    print(fg('green') + "\n\tScaling {} to {}".format(name, desired) + reset)

    update_service(desired_count=desired, cluster=cluster, service_name=name)


@main.command(name='redeploy')
@click.argument('name')
def cmd_redeploy(name):
    """Force redeployment of a Service"""

    cluster = get_default_cluster()

    print(fg('green') + "\n\tRedeploying " + name + reset)

    update_service(force_new_deployment=True, cluster=cluster, service_name=name)

@main.command(name='create')
@click.argument('name')
@click.option('--rev', type=click.IntRange(0, 1000))
@click.option('--desired', default=2, type=click.IntRange(1, 16))
def cmd_create_service(name, rev, desired):
    """Create Service"""

    cluster = get_default_cluster()

    if not rev:
        containerDefinitions = get_container_defs_from_file("./services/{}.yaml".format(name), cluster)
        rev = register_task_def(family=name, containerDefinitions=containerDefinitions)

    taskdef = "{}:{}".format(name, rev) if rev else name
    print(fg('green') + "\n\tCreating {} with revision {}".format(name, rev) + reset)

    create_service(task_definition=taskdef, cluster=cluster, desired_count=desired, service_name=name)


@main.command(name='update')
@click.argument('name')
@click.option('--rev', type=click.IntRange(0, 1000))
@click.option('--desired', help="Desired Count", default=2, type=click.IntRange(1, 16))
def cmd_update_service(name, rev, desired):
    """Update Service"""

    cluster = get_default_cluster()

    if not rev:
        containerDefinitions = get_container_defs_from_file("./services/{}.yaml".format(name), cluster)
        rev = register_task_def(family=name, containerDefinitions=containerDefinitions)

    taskdef = "{}:{}".format(name, rev) if rev else name
    print(fg('green') + "\n\tUpdating {} using revision {}".format(name, rev) + reset)

    update_service(task_definition=taskdef, cluster=cluster, desired_count=desired, service_name=name)


@main.command(name='delete')
@click.argument('name')
def cmd_delete(name):
    """Delete Service"""

    cluster = get_default_cluster()

    print(fg('red') + "\n\tDeleting {}".format(name) + reset)

    # Scale down first
    update_service(desired_count=0, cluster=cluster, service_name=name)

    delete_service(cluster=cluster, service_name=name)


if __name__ == '__main__':

    try:
        main(obj={})

    except (KeyboardInterrupt, SystemExit):
        log.info("Shutting down..")
    except Exception as e:
        log.exception("Something bad happened, Shutting Down...")
        exit(e)