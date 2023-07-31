CDK deployment
==============
Deploy
~~~~~~
You will need to make a **copy** of app_template_dev.py file with a different name (app__dev.py) and keep a copy of
it locally so that it will not be committed. In your own copy there are two important configuration
items which you can alter:

#. AWS_PROFILE ()
#. Your initials when deploying to an AWS account with multiple users ()

To deploy, first set the appropriate environment variables::

        export AWS_PROFILE=<profile>
You'll then need to synthesize the CDK code with the command::

        cdk synth --app "python app_template_dev.py"
Then you can deploy the architecture with the following command::

    cdk deploy --app "python app_template_dev.py" [ stack | --all ]

After about 20-30 minutes or so, you should have a brand-new SDS set up in AWS.
This is the repository for the cloud infrastructure on the IMAP mission.

Important
~~~~~~~~~
If you do not intend to use AWS resources for more than a couple of days do a destroy to avoid charges,
especially with databases.::

        cdk destroy --app "python app_template_dev.py" [ stack | --all ]