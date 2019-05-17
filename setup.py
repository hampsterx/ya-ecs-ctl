from setuptools import setup

with open('README.md') as f:
    long_description = f.read()

setup(name='ya-ecs-ctl',
      description='AWS ECS Control Tool',
      long_description=long_description,
      long_description_content_type="text/markdown",
      version='0.2.4',
      url='https://github.com/hampsterx/ya-ecs-ctl',
      author='Tim van der Hulst',
      author_email='tim.vdh@gmail.com',
      license='Apache2',
      classifiers=[
          'Development Status :: 4 - Beta',
          'Intended Audience :: System Administrators',
          'License :: OSI Approved :: Apache Software License',
          'Programming Language :: Python :: 2'
      ],
      packages=['ya_ecs_ctl'],
      install_requires=[
            'click>=6.6',
            'boto3>=1.7.50',
            'terminaltables==3.1.0',
            'humanize==0.5.1',
            'EasySettings==2.1.0',
            'prompt-toolkit==2.0.4',
            'PyYAML>=5.1',
            'colored==1.3.5',
            'Jinja2>=2.8',
      ],
      entry_points={
          'console_scripts': [
              'ecs=ya_ecs_ctl.main:main'
          ]
      }
)