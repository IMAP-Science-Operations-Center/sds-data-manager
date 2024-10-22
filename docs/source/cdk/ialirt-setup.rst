I-ALiRT Setup
=============

Secrets Manager
~~~~~~~~~

Ensure you have a secret in AWS Secrets Manager with a username and password for the Nexus repo. The secret should be named `nexus-repo` and can be created using the following command::

    aws secretsmanager create-secret --name nexus-repo --description "Credentials for Nexus Docker registry" --secret-string '{"username":"your-username", "password":"your-password"}'

Image Versioning
~~~~~~~~~
We will rely on semantic versioning for the images MAJOR.MINOR (e.g., 1.0).

- MAJOR: Major changes.
- MINOR: Minor changes.

For development we will keep the major changes at 0.

Nexus Repo
~~~~~~~~~
#. Check that you are not already logged in by running::

    cat ~/.docker/config.json

#. Ensure that the Nexus registry URL is not in the list of logged in registries.
#. Run the following command to login (you will be prompted for your WebIAM username and password)::

    docker login docker-registry.pdmz.lasp.colorado.edu
#.  Your `~/.docker/config.json` file should now contain a reference to the registry url.
#.  Determine the appropriate version for your image based on the semantic versioning scheme (MAJOR.MINOR).
#. Build the image and tag it with the Nexus registry URL::

    docker build -t ialirt:X.Y --rm . --no-cache

#. Tag with the Nexus registry URL::

    docker tag ialirt:X.Y docker-registry.pdmz.lasp.colorado.edu/ialirt/ialirt:X.Y

#. Push the image::

    docker push docker-registry.pdmz.lasp.colorado.edu/ialirt/ialirt:X.Y
#. Images may be viewed on the Nexus website: https://artifacts.pdmz.lasp.colorado.edu

ECS Recognition of a New Image
~~~~~~~~~~~~~
To have ECS recognize a new image the cdk must be redeployed.
