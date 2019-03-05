# Yet Another ECS CTL tool

```pip install ya-ecs-ctl```

Adds binary:


```ecs```

```
Usage: ecs [OPTIONS] COMMAND [ARGS]...

Options:
  --help  Show this message and exit.

Commands:
  ci       Interact with Container Instances
  cluster  Interact with Cluster
  ec2      Interact with EC2 Instances
  repo     Interact with (Container) Repos
  service  Interact with Service
  task     Interact with Task


```

## Getting Started

Ensure aws cli tool works and you have run aws configure. Uses boto3 and assumes the following ENVs are set:

- AWS_ACCESS_KEY_ID
- AWS_SECRET_ACCESS_KEY

Your default region should be set. Verify it with

    cat ~/.aws/config 
    
    [default]
    region=us-west-1

Run 

    ecs service ls

It will first ask you to select a default cluster.
Currently this tool does not have a command to create one.

This preference is saved here:

    cat .settings.conf 
    
    {
        "cluster": "Dev-Apps"
    }


Lets try that again..

    ecs service ls

	Cluster: Dev-Apps
    
    +------------------------+---------------------------+-------------+---------+---------+---------+--------+--------------+----------------------------+
    | Service Name           | Task Def                  | Launch Type | Desired | Running | Pending | Status | Created      | Deployments (des/pend/run) |
    +------------------------+---------------------------+-------------+---------+---------+---------+--------+--------------+----------------------------+
    | my-app                 | my-app:122                | EC2         | 2       | 2       | 0       | ACTIVE | 4 months ago | 2/0/2 6 days ago           |
    | another-app            | another-app:1             | EC2         | 2       | 2       | 0       | ACTIVE | a day ago    | 2/0/2 a day ago            |
    +------------------------+---------------------------+-------------+---------+---------+---------+--------+--------------+----------------------------+

### Service Commands

    Usage: ecs service [OPTIONS] COMMAND [ARGS]...
    
      Interact with Service
    
    Options:
      --help  Show this message and exit.
    
    Commands:
      create    Create Service
      delete    Delete Service
      describe  Describe Service
      ls        List Services
      redeploy  Force redeployment of a Service
      scale     Scale Service
      tasks     List Tasks for Service
      update    Update Service


See "examples" folder for config structure of services.
Support is provided for FARGATE, Scheduled Tasks, etc.


## Alternatives..

https://github.com/diegoacuna/ecs-ctl
> Manage Amazon ECS like with kubectl.

https://github.com/labd/ecs-deplojo/
> Deployment tool for Amazon ECS.

https://github.com/fabfuel/ecs-deploy
> ecs-deploy simplifies deployments on Amazon ECS by providing a convinience CLI tool for complex actions, which are executed pretty often.

https://github.com/cuttlesoft/ecs-deploy.py
> Python script to instigate an automatic blue/green deployment using the Task Definition and Service entities in Amazon's ECS.

https://github.com/boroivanov/ecs-tools
> 
ECS Tools cli aims to make deploying to ECS Fargate easier. It also provides an easy way to scale and update environment variables.




