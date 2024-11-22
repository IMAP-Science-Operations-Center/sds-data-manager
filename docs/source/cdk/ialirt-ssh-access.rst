I-ALiRT IT SSH Access
==================

Create IAM Role for IT
~~~~~~~~~
SDC will have already created key-pairs and associated them with the EC2 instances. Note each keypair is required to be restricted::

    chmod 600 <keypair-name.pem>

SDC will create the IAM user, attach the AmazonSSMFullAccess Policy, and securely provide access keys for the user::

    aws iam create-user --user-name <user>
    aws iam attach-user-policy --user-name <user> --policy-arn arn:aws:iam::aws:policy/AmazonSSMFullAccess
    aws iam create-access-key --user-name <user>

IT should then follow the directions in Section "Existing User" on this page:
:ref:`aws-setup`

Setting Up IT SSH Key Pair
~~~~~~~~~

IT will generate a SSH key pair on their machine::

    ssh-keygen -t rsa -b 2048 -f ~/.ssh/ialirt_key

This command will create two files: a private key (rsa_name) and a public key (rsa_name.pub). IT will keep the private key secure and send the SDC the public key. The SDC will start a Services Systems Manager (SSM) session::

    aws ssm start-session --target <EC2 instance ID> --document-name AWS-StartPortForwardingSessionToRemoteHost --parameters '{"host":["127.0.0.1"],"portNumber":["22"]}'

This command will result in a port being opened (as printed in the terminal). The SDC will use their .pem file to SSH into the EC2 instance::

    ssh -i <keypair-name.pem> -p <port> ec2-user@127.0.0.1

Once logged in change directories::

    cd ~/.ssh

And paste the public key at the end of the authorized_keys file. Ensure the .ssh directory and authorized_keys file have the correct permissions::

    vi authorized_keys

- Press i to enter insert mode.
- Use the arrow keys to navigate to the end of the file
- Press Enter to create a new line below the existing key.
- Copy the new public key to your clipboard and paste it into the file.
- Press Esc. Type :wq and press Enter.
Ensure the .ssh directory and authorized_keys file have the correct permissions::

    chmod 700 ~/.ssh
    chmod 600 ~/.ssh/authorized_keys

IT should now be able to connect to the EC2 instance using their private key::

    aws ssm start-session --target <EC2 instance ID> --document-name AWS-StartPortForwardingSessionToRemoteHost --parameters '{"host":["127.0.0.1"],"portNumber":["22"]}’
    ssh -i <rsa_name> -p <port> ec2-user@127.0.0.1

Now any command required to connect to rsync can be performed.
To exit the ssh session, type exit and press Enter.
To exit the SSM session::

    aws ssm describe-sessions --state Active

Note the session id then::

    aws ssm terminate-session --session-id <session-id>

