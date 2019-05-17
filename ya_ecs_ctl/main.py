#!/usr/bin/env python3
import os
import json
import yaml
import click
import logging
import boto3
import pprint
import copy
from easysettings import JSONSettings as Settings
from prompt_toolkit import prompt
from ya_ecs_ctl.utils import ChoicesCompleter, ChoicesValidator, lowerCaseFirstLetter, change_keys, chunks, dump, reset, print_table
from botocore.exceptions import ClientError
from colored import fg
from jinja2 import Template
import humanize
import datetime

settings = Settings()

settings_file = ".settings.conf"
if not os.path.exists(settings_file):
    settings.save(settings_file)
else:
    settings.load(settings_file)


log = logging.getLogger(__name__)

# Boto objects
ecr = boto3.client('ecr')
ecs = boto3.client('ecs')
ec2 = boto3.client('ec2')
elb = boto3.client('elbv2')
events = boto3.client('events')
logs = boto3.client('logs')


def get_cluster_ids(ecs):
    clusters = ecs.list_clusters()['clusterArns']
    return [c.split(':cluster/')[1] for c in clusters]


def get_clusters_info(cluster_ids):
    return ecs.describe_clusters(clusters=cluster_ids)['clusters']



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

def get_cluster_arn(name):

    clusters = ecs.list_clusters()['clusterArns']

    cluster = [c for c in clusters if c.endswith("/{}".format(name))]

    if cluster:
        return cluster[0]

    return None



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
        i['Name'] if i['Name'] else '',
        i['AvailabilityZone'],
        i['PrivateIpAddress'],
        i['ImageId'],
        i['InstanceType'],
        i['InstanceId'],
        i['State'],
        humanize.naturaltime(datetime.datetime.now(datetime.timezone.utc) - i['LaunchTime']),
    ] for i in instances]

    data = sorted(data,key=lambda x:x[0])

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


def get_container_repos():

    repos = ecr.describe_repositories(maxResults=100)['repositories']

    repos = [{'name' : r['repositoryName']} for r in repos]

    for r in repos:


        images = ecr.describe_images(repositoryName=r['name'], maxResults=100)

        if 'nextToken' in images:
            # todo
            raise NotImplementedError()

        images = images['imageDetails']

        r['images'] = [{'tags': i.get('imageTags',[]), 'digest': i['imageDigest'], 'size': i['imageSizeInBytes'], 'date': i['imagePushedAt']} for i in images]

    return repos


def print_container_repos(repos):

    header = ['Name', 'Latest', 'Recent Tags']

    def format_latest_image(images):
        latest = sorted([i for i in images if 'latest' in i['tags']], key=lambda x:x['date'])

        return "({}) {}".format(latest[0]['digest'].split("sha256:")[-1][0:8], humanize.naturaltime(datetime.datetime.now(datetime.timezone.utc) - latest[0]['date']).rjust(16)) if latest else ""

    def format_recent_tag_images(images):
        tagged = sorted([i for i in images if 'latest' not in i['tags'] and i['tags']], key=lambda x: x['date'])[0:3]

        if not tagged:
            return ""

        tagged = ["({}) [{}] {}".format(t['digest'].split("sha256:")[-1][0:8], ",".join(t['tags']), humanize.naturaltime(datetime.datetime.now(datetime.timezone.utc) - t['date'])) for t in tagged]

        return ", ".join(tagged)

    data = [[
        r['name'],
        format_latest_image(r['images']),
        format_recent_tag_images(r['images'])
    ] for r in repos]

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

    data = []

    for t in tasks:

        item = [
            t['group'],
            format_container_tasks(t['containers']),
            t['taskDefinitionArn'].split(':task-definition/')[1],
            format_container_ports(t['containers']),
            t['container_instance']['ec2Detail']['Name'] if "container_instance" in t else "NA",
            t['container_instance']['ec2Detail']['PrivateIpAddress'] if "container_instance" in t else "NA",
            t['container_instance']['ecs.availability-zone'] if "container_instance" in t else "NA",
            t['container_instance']['ecs.instance-type'] if "container_instance" in t else "NA",
            t.get('connectivity', ''),
            humanize.naturaltime(datetime.datetime.now(datetime.timezone.utc) - t['connectivityAt']) if 'connectivityAt' in t else "",
            t['memory'],
            t['desiredStatus'],
            t['healthStatus'],
            t['lastStatus'],
        ]

        data.append(item)

    print_table(header, data)


def print_services(services):

    header = ['Service Name', 'Task Def', 'Launch Type', 'Desired', 'Running', 'Pending', 'Status', 'Created', 'Deployments (des/pend/run)']

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

def assert200Response(response):
    status = response['ResponseMetadata']['HTTPStatusCode']
    if status != 200:
        raise Exception("Something went wrong: status={}".format(status))

def delete_service(cluster, service_name):
    response = ecs.delete_service(service=service_name, cluster=cluster)

    assert200Response(response)

    return True

def delete_schedule(name):

    try:
        response = events.list_targets_by_rule(
            Rule=name
        )
    except events.exceptions.ResourceNotFoundException:
        print(fg('red') + "\n\t" + "Schedule not found" + reset)
        return

    assert200Response(response)

    target_ids = [t['Id'] for t in response['Targets']]

    if target_ids:
        response = events.remove_targets(
            Rule=name,
            Ids=target_ids
        )

        assert200Response(response)

    response = events.delete_rule(
        Name=name,
    )

    assert200Response(response)

def create_schedule_expression(fixed_interval=None):

    if fixed_interval:
        if fixed_interval.endswith("m"):
            return "rate({} minutes)".format(fixed_interval.split("m")[0])
        elif fixed_interval.endswith("h"):
            return "rate({} hours)".format(fixed_interval.split("h")[0])

    raise NotImplementedError("Dont know how to convert this schedule: {}".format(fixed_interval))


def create_schedule(name,role_arn, launch_type, cluster_arn, task_arn, network_configuration, fixed_interval=None):

    schedule = create_schedule_expression(fixed_interval)

    response = events.put_rule(
        Name=name,
        ScheduleExpression=schedule,
        State='ENABLED',
        Description="ECS Task {} on {}".format(name, fixed_interval),
    )

    if launch_type== "FARGATE" and network_configuration:
        if "AwsvpcConfiguration" not in network_configuration:
            raise NotImplementedError("Expected AwsvpcConfiguration inside NetworkConfiguration")

        subnets = network_configuration['AwsvpcConfiguration']['Subnets']
        security_groups = network_configuration['AwsvpcConfiguration']['SecurityGroups']

        response = events.put_targets(
            Rule=name,
            Targets=[
                {
                    'Id': name,
                    'Arn': cluster_arn,
                    "RoleArn": role_arn,
                    "EcsParameters": {
                        "TaskDefinitionArn": task_arn,
                        "TaskCount": 1,
                        "LaunchType": "FARGATE",
                        "NetworkConfiguration": {
                            "awsvpcConfiguration": {
                                "Subnets": subnets,
                                "SecurityGroups": security_groups,
                                "AssignPublicIp": "DISABLED"
                            }
                        },
                        "PlatformVersion": "LATEST"
                    }
                }
            ]
        )
    else:
        response = events.put_targets(
            Rule=name,
            Targets=[
                {
                    'Id': name,
                    'Arn': cluster_arn,
                    "RoleArn": role_arn,
                    "EcsParameters": {
                        "TaskDefinitionArn": task_arn,
                        "TaskCount": 1,
                        "LaunchType": "EC2"
                    }
                }
            ]
        )

    assert200Response(response)


def create_service(cluster, service_name,
                   placement_strategy=None,
                   launch_type=None,
                   placement_constraints=None,
                   deployment_configuration=None,
                   network_configuration=None,
                   scheduling_strategy=None,
                   task_definition=None, desired_count=None):
    params = {}

    if launch_type:
        params['launchType'] = launch_type

    if scheduling_strategy != 'DAEMON':

        if desired_count is not None:
            params['desiredCount'] = desired_count

        if placement_strategy is not None and launch_type != "FARGATE":
            params['placementStrategy'] = placement_strategy

    if scheduling_strategy is not None and launch_type != "FARGATE":
        params['schedulingStrategy'] = scheduling_strategy

    if placement_constraints is not None and launch_type != "FARGATE":
        params["placementConstraints"] = [
            {'type': pc['Type'], 'expression': pc['Expression']} for pc in placement_constraints
        ]

    if deployment_configuration is not None and launch_type != "FARGATE":
        params["deploymentConfiguration"] = {}
        if 'MaximumPercent' in deployment_configuration:
            params["deploymentConfiguration"]['maximumPercent'] = deployment_configuration['MaximumPercent']
        if 'MinimumHealthyPercent' in deployment_configuration:
            params["deploymentConfiguration"]['minimumHealthyPercent'] = deployment_configuration['MinimumHealthyPercent']

    if network_configuration:
        aws_vpc_config = copy.copy(network_configuration['AwsvpcConfiguration'])

        params['networkConfiguration'] = {
            'awsvpcConfiguration' : {
                'subnets': aws_vpc_config.pop("Subnets") ,
                'securityGroups': aws_vpc_config.pop("SecurityGroups")
            }
        }


    params['taskDefinition'] = task_definition

    # dump(params)

    response = ecs.create_service(serviceName=service_name, cluster=cluster, **params)

    assert200Response(response)


def update_service(cluster, service_name, task_definition=None, scheduling_strategy=None, force_new_deployment=None, desired_count=None):
    params = {}

    if force_new_deployment:
        params['forceNewDeployment'] = True

    if scheduling_strategy != 'DAEMON':
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


@main.group(name='cluster')
def cmd_cluster():
    """
    Interact with Cluster
    """
    pass

@cmd_cluster.command(name='ls')
def cmd_cluster_ls():
    """Display Clusters Info"""
    cluster_ids = get_cluster_ids(ecs)
    clusters_info = get_clusters_info(cluster_ids)

    print_clusters_info(clusters_info)

@cmd_cluster.command(name='switch')
@click.option('-n', help="Name of Cluster")
def cmd_switch_cluster(n):
    """Switch Default Cluster"""

    cluster_name = None

    if n:
        cluster_ids = get_cluster_ids(ecs)
        clusters_info = get_clusters_info(cluster_ids)
        cluster_names = [c['clusterName'] for c in clusters_info]
        if n in cluster_names:
            cluster_name = n

    settings.setsave('cluster', cluster_name)

    get_default_cluster()



@main.group(name='ci')
def cmd_container_instances():
    """
    Interact with Container Instances
    """
    pass


@cmd_container_instances.command(name='drain')
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


@cmd_container_instances.command(name='ls')
def cmd_container_instances_ls():
    """List container Instances"""

    cluster = get_default_cluster()

    instances = get_container_instances_by_cluster_name(cluster)

    print_container_instances(instances)


@main.group(name='ec2')
def cmd_ec2():
    """
    Interact with EC2 Instances
    """
    pass


@cmd_ec2.command(name='ls')
def cmd_ec2_instances_ls():
    """List ec2 Instances"""

    instances = get_ec2_instances()

    print_ec2_instances(instances)



@main.group(name='task')
def cmd_task():
    """
    Interact with Task
    """
    pass


@cmd_task.command(name='register')
@click.argument('name')
def cmd_register(name):
    """Register task definition"""

    cluster = get_default_cluster()

    service_def = get_service_def_from_file(name, cluster)
    task_arn = register_task_def(service_def['TaskDefinition'])
    rev = task_arn.split(":")[-1]


    print(fg('green') + "\n\t{} now at revision {}".format(name, rev) + reset)


@cmd_task.command(name='start')
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

@cmd_task.command(name='stop')
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

def get_service_def_from_file(name, cluster_name):

    file_path = "./services/{}/{}.yaml".format(cluster_name, name)

    shared_config_path = "./services/{}.yaml".format(cluster_name)

    shared_config = {
        'Properties': {},
        'LogConfiguration': {

        }

    }

    if os.path.exists(shared_config_path):
        with open(shared_config_path, 'r') as f:
            shared_config = yaml.load(f, Loader=yaml.SafeLoader)

    shared_config['Properties'].update(
        {
            'CLUSTER_NAME': cluster_name,
            'REGION': get_default_region()
        }
    )

    with open(file_path, 'r') as f:
        service_def = f.read()

        template = Template(service_def)

        service_def = template.render(shared_config['Properties'])

        service_def = yaml.load(service_def, Loader=yaml.SafeLoader)

    task_def =  service_def['TaskDefinition']

    task_def = change_keys(task_def, convert=lowerCaseFirstLetter)

    log_config = change_keys(shared_config['LogConfiguration'], convert=lowerCaseFirstLetter)

    log_config = json.dumps(log_config)

    template = Template(log_config)

    shared_config['Properties'].update({'FAMILY': task_def['family']})

    log_config = template.render(shared_config['Properties'])

    log_config = json.loads(log_config)

    for cd in task_def['containerDefinitions']:
        cd['logConfiguration'] = log_config

    service_def['TaskDefinition'] = task_def

    return service_def

def create_log_group(name):
    result = logs.describe_log_groups(logGroupNamePrefix=name)['logGroups']

    for lg in result:
        if lg['logGroupName'] == name:
            return

    logs.create_log_group(logGroupName=name)
    print(fg('green') + "\n\tCreated log group {}".format(name) + reset)


def register_task_def(task_def):

    # Ensure log group exists (or will fail and be hard to diagnose)
    log_groups = list(set([c['logConfiguration']['options']['awslogs-group'] for c in task_def['containerDefinitions']
                           if c.get('logConfiguration', {}).get('logDriver') == 'awslogs']))

    for lg in log_groups:
        create_log_group(lg)

    result = ecs.register_task_definition(**task_def)

    if result['ResponseMetadata']['HTTPStatusCode'] != 200:
        print(result['ResponseMetadata'])
        raise Exception()

    return result['taskDefinition']['taskDefinitionArn']

@main.group(name='repo')
def cmd_repos():
    """
    Interact with (Container) Repos
    """
    pass


@cmd_repos.command(name='ls')
def cmd_list_repos():
    """List Repos"""

    print(fg('green') + "\n\tRegion: {}".format(boto3.session.Session().region_name) + reset)

    repos = get_container_repos()
    print_container_repos(repos)


@cmd_repos.command(name='create')
@click.argument('name')
def cmd_create_repo(name):
    """Create Repo"""

    result = ecr.create_repository(repositoryName=name)

    if result['ResponseMetadata']['HTTPStatusCode'] == 200:
        print(fg('green') + "\n\tCreated {}".format(result['repository']['repositoryUri']) + reset)
    else:
        pprint.pprint(result)

@cmd_repos.command(name='delete')
@click.argument('name')
@click.option('--force', is_flag=True)
def cmd_delete_repo(name, force):
    """Delete Repo"""

    try:
        result = ecr.delete_repository(repositoryName=name, force=force)

        if result['ResponseMetadata']['HTTPStatusCode'] == 200:
            print(fg('green') + "\n\tDeleted OK" + reset)

    except Exception as e:
        if "cannot be deleted because it still contains images" in str(e):
            print(fg('red') + "\n\t" + "Repo {} contains images. use --force flag".format(name) + reset)
        else:
            print(fg('red') + "\n\t" + str(e) + reset)


@main.group(name='service')
def cmd_service():
    """
    Interact with Service
    """
    pass


@cmd_service.command(name='ls')
def cmd_list_services():
    """List Services"""

    cluster = get_default_cluster()

    services = get_services_by_cluster_name(cluster)
    print_services(services)


@cmd_service.command(name='tasks')
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

    task_ids = get_task_ids_by_family_and_cluster(family=service, cluster=cluster)

    if task_ids:
        tasks = get_tasks_by_ids_and_cluster(task_ids, cluster)

        container_instance_ids = [t['containerInstanceArn'] for t in tasks if 'containerInstanceArn' in t]

        if container_instance_ids:
            results = get_container_instances_by_ids(ids=container_instance_ids, cluster=cluster)

            container_instances_dict = {c['containerInstanceArn']:c for c in results}

            for t in tasks:
                t['container_instance'] = container_instances_dict[t['containerInstanceArn']]

        print_tasks(tasks)


@cmd_service.command(name='scale')
@click.argument('name')
@click.argument('desired', type=click.IntRange(0, 16))
def cmd_scale(name, desired):
    """Scale Service"""

    cluster = get_default_cluster()

    print(fg('green') + "\n\tScaling {} to {}".format(name, desired) + reset)

    update_service(desired_count=desired, cluster=cluster, service_name=name)


@cmd_service.command(name='redeploy')
@click.argument('name')
def cmd_redeploy(name):
    """Force redeployment of a Service"""

    cluster = get_default_cluster()

    print(fg('green') + "\n\tRedeploying " + name + reset)

    update_service(force_new_deployment=True, cluster=cluster, service_name=name)


@cmd_service.command(name='create')
@click.argument('name')
@click.option('--desired', default=1, type=click.IntRange(1, 16))
def cmd_create_service(name, desired):
    """Create Service"""

    cluster = get_default_cluster()

    service_def = get_service_def_from_file(name, cluster)

    task_arn = register_task_def(service_def['TaskDefinition'])

    rev = task_arn.split(":")[-1]

    scheduling_strategy = "REPLICA"
    if "SchedulingStrategy" in service_def:
        scheduling_strategy = service_def["SchedulingStrategy"]

    if 'Desired' in service_def:
        desired = int(service_def['Desired'])

    placement_strategy = [
        {
            'type': 'spread',
            'field': 'attribute:ecs.availability-zone'
        },
        {
            "field": "instanceId",
            "type": "spread"
        }
    ]

    placement_constraints = service_def.get("PlacementConstraints", None)

    deployment_configuration = service_def.get("DeploymentConfiguration", None)

    launch_type = service_def.get("LaunchType", None)

    network_configuration = service_def.get("NetworkConfiguration", None)

    print(fg('green') + "\n\tCreating Service {} (Desired={}) with revision {}".format(name, desired, rev) + reset)

    create_service(task_definition="{}:{}".format(name, rev) if rev else name,
                   placement_strategy=placement_strategy,
                   placement_constraints=placement_constraints,
                   deployment_configuration=deployment_configuration,
                   scheduling_strategy=scheduling_strategy,
                   network_configuration=network_configuration,
                   launch_type=launch_type,
                   cluster=cluster, desired_count=desired, service_name=name)

    schedule = service_def.get("Schedule", None)

    cluster_arn = get_cluster_arn(cluster)

    if schedule:
        schedule_name = "{}-{}".format(cluster, name)

        fixed_interval = schedule.get("FixedInterval", None)

        role_arn = schedule.get('RoleARN')

        network_configuration = service_def.get("NetworkConfiguration", None)

        print(fg('green') + "\n\tCreating Schedule {} ({}) with revision {}".format(schedule_name, fixed_interval if fixed_interval else "TODO", rev) + reset)

        create_schedule(
            name=schedule_name,
            role_arn=role_arn,
            task_arn=task_arn,
            launch_type=launch_type,
            network_configuration=network_configuration,
            cluster_arn=cluster_arn,
            fixed_interval=fixed_interval,
        )


@cmd_service.command(name='update')
@click.argument('name')
@click.option('--rev', type=click.IntRange(0, 1000))
@click.option('--desired', help="Desired Count", default=2, type=click.IntRange(1, 16))
def cmd_update_service(name, rev, desired):
    """Update Service"""

    cluster = get_default_cluster()

    if not rev:
        service_def = get_service_def_from_file(name, cluster)
        task_arn = register_task_def(service_def['TaskDefinition'])
        rev = task_arn.split(":")[-1]

        scheduling_strategy = "REPLICA"
        if "SchedulingStrategy" in service_def:
            scheduling_strategy = service_def["SchedulingStrategy"]

        if 'Desired' in service_def:
            desired = int(service_def['Desired'])

    taskdef = "{}:{}".format(name, rev) if rev else name
    print(fg('green') + "\n\tUpdating {} (Desired={}) using revision {}".format(name, desired, rev) + reset)

    update_service(task_definition=taskdef, cluster=cluster, scheduling_strategy=scheduling_strategy, desired_count=desired, service_name=name)

@cmd_service.command(name='describe')
@click.argument('name')
def cmd_describe_service(name):
    """Describe Service"""

    cluster = get_default_cluster()

    service_def = get_service_def_from_file(name, cluster)

    pprint.pprint(service_def)


@cmd_service.command(name='delete')
@click.argument('name')
def cmd_delete(name):
    """Delete Service"""

    cluster = get_default_cluster()

    print(fg('red') + "\n\tDeleting {}".format(name) + reset)


    # Scale down first
    try:
        update_service(desired_count=0, cluster=cluster, service_name=name)
    except ClientError:
        pass

    delete_service(cluster=cluster, service_name=name)

    # delete schedule

    service_def = get_service_def_from_file(name, cluster)

    if "Schedule" in service_def:
        schedule_name = "{}-{}".format(cluster, name)
        delete_schedule(schedule_name)


if __name__ == '__main__':

    try:
        main(obj={})

    except (KeyboardInterrupt, SystemExit):
        log.info("Shutting down..")
    except Exception as e:
        log.exception("Something bad happened, Shutting Down...")
        exit(e)
