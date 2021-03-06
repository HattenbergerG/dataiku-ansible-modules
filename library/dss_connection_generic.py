#!/usr/bin/env python2gt

from __future__ import absolute_import
import six
ANSIBLE_METADATA = {
    'metadata_version': '1.1',
    'status': ['preview'],
    'supported_by': 'dataiku-ansible-modules'
}

DOCUMENTATION = '''
---
module: dss_connection_generic

short_description: Creates, edit or delete a Data Science Studio connection of any kind

description:
    - "This module edits a complete connection"

options:
    connect_to:
        description:
            - A dictionary containing "port" and "api_key". This parameter is a short hand to be used with dss_get_credentials
        required: true
    host:
        description:
            - The host on which to make the requests.
        required: false
        default: localhost
    port:
        description:
            - The port on which to make the requests.
        required: false
        default: 80
    api_key:
        description:
            - The API Key to authenticate on the API. Mandatory if connect_to is not used
        required: false
    name:
        description:
            - Name of the connection
        required: true
    connection_args:
        description:
            - A dictionary of additional arguments passed into the json of the connection.
        required: true
    state:
        description:
            - Wether the connection is supposed to exist or not. Possible values are "present" and "absent"
        default: present
        required: false
author:
    - Jean-Bernard Jansen (jean-bernard.jansen@dataiku.com)
'''

EXAMPLES = '''
# Creates a group using dss_get_credentials if you have SSH Access
- name: Get the API Key
  become: true
  become_user: dataiku
  dss_get_credentials:
    datadir: /home/dataiku/dss
    api_key_name: myadminkey
  register: dss_connection_info
'''

RETURN = '''
previous_connection_def:
    description: The previous values
    type: dict
connection_def:
    description: The current values if the connection have not been deleted
    type: dict
message:
    description: CREATED, MODIFIED, UNCHANGED or DELETED 
    type: str
'''

from ansible.module_utils.basic import AnsibleModule
from dataikuapi import DSSClient
from dataikuapi.dss.admin import DSSConnection
from dataikuapi.utils import DataikuException
import copy
import traceback
import re
import time
import collections

# Trick to expose dictionary as python args
class MakeNamespace(object):
    def __init__(self,values):
        self.__dict__.update(values)

# Similar to dict.update but deep
def update(d, u):
    for k, v in six.iteritems(u):
        if isinstance(v, collections.Mapping):
            d[k] = update(d.get(k, {}), v)
        else:
            d[k] = v
    return d


connection_template = {
    "allowManagedDatasets": True,
    "allowManagedFolders": False,
    "allowWrite": True,
    "allowedGroups": [],
    #"creationTag": {},
    #"credentialsMode": "GLOBAL", 
    "detailsReadability": {
	"allowedGroups": [], 
	"readableBy": "NONE"
    },
    #"indexingSettings": {
	#"indexForeignKeys": False,
	#"indexIndices": False,
	#"indexSystemTables": False
    #},
    "maxActivities": 0,
    #"name": "",
    "params": {
    }, 
    #"type": "PostgreSQL",
    "usableBy": "ALL", 
    "useGlobalProxy": False
}

encrypted_fields_list = ["password"]
pytypefunc=type 

def run_module():
    # define the available arguments/parameters that a user can pass to
    # the module
    module_args = dict(
        connect_to=dict(type='dict', required=False, default={}, no_log=True),
        host=dict(type='str', required=False, default="127.0.0.1"),
        port=dict(type='str', required=False, default=None),
        api_key=dict(type='str', required=False, default=None),
        name=dict(type='str', required=True),
        state=dict(type='str', required=False, default="present"),
        type=dict(type='str', required=True),
        connection_args=dict(type='dict', default={}, required=False),
        #params=dict(type='dict', default={}, required=False),
    )

    module = AnsibleModule(
        argument_spec=module_args,
        supports_check_mode=True
    )

    args = MakeNamespace(module.params)
    if args.state not in ["present","absent"]:
        module.fail_json(msg="Invalid value '{}' for argument state : must be either 'present' or 'absent'".format(args.source_type))
    api_key = args.api_key if args.api_key is not None else args.connect_to.get("api_key",None)
    if api_key is None:
        module.fail_json(msg="Missing an API Key, either from 'api_key' or 'connect_to' parameters".format(args.state))
    port = args.port if args.port is not None else args.connect_to.get("port","80")
    host = args.host
    type = args.type

    result = dict(
        changed=False,
        message='UNCHANGED',
    )

    try:
        client = DSSClient("http://{}:{}".format(args.host, port),api_key=api_key)
        exists = True
        create = False
        connection = client.get_connection(args.name)
        current_def = None
        try:
            current_def  = connection.get_definition()
        except DataikuException as e:
            #if e.message.startswith("com.dataiku.dip.server.controllers.NotFoundException"):
            if str(e) == "java.lang.IllegalArgumentException: Connection '{}' does not exist".format(args.name):
                exists = False
                if args.state == "present":
                    create = True
            else:
                raise
        except Exception as e:
            raise

        current_def = None
        encrypted_fields_before_change = {"params":{}}
        if exists:
            result["previous_group_def"] = current_def = connection.get_definition()
            # Check this is the same type
            if current_def["type"] != type:
                module.fail_json(msg="Connection '{}' already exists but is of type '{}'".format(args.name,current_def["type"]))
                return
            # Remove some values from the current def
            for field in encrypted_fields_list:
                encrypted_field_before_change = current_def["params"].get(field,None)
                if encrypted_field_before_change is not None:
                    encrypted_fields_before_change["params"][field] = encrypted_field_before_change
                    del current_def["params"][field]
        else:
            if args.state == "present":
                #for mandatory_create_param in ["user", "password", "database", "postgresql_host"]:
                    #if module.params[mandatory_create_param] is None:
                        #module.fail_json(msg="Connection '{}' does not exist and cannot be created without the '{}' parameter".format(args.name,mandatory_create_param))
                pass

        # Build the new definition
        new_def = copy.deepcopy(current_def) if exists else connection_template # Used for modification

        # Apply every attribute except the password for now
        new_def["name"] = args.name
        update(new_def, args.connection_args)

        # Extract args that may be encrypted
        encrypted_fields = {"params":{}}
        for field in encrypted_fields_list:
            value = new_def["params"].get(field,None)
            if value is not None:
                encrypted_fields["params"][field] = value
                del new_def["params"][field]

        # Prepare the result for dry-run mode
        result["changed"] = create or (exists and args.state == "absent") or (exists and current_def != new_def)
        if result["changed"]:
            if create:
                result["message"] = "CREATED"
            elif exists:
                if  args.state == "absent":
                    result["message"] = "DELETED"
                elif current_def != new_def:
                    result["message"] = "MODIFIED"

        if args.state == "present":
            result["connection_def"] = new_def

        if module.check_mode:
            module.exit_json(**result)

        ## Apply the changes
        if result["changed"] or (0 < len(encrypted_fields["params"]) and exists):
            if create:
                update(new_def, encrypted_fields)
                connection = client.create_connection(args.name, type, new_def["params"])
                def_after_creation = connection.get_definition()
                update(def_after_creation,new_def)
                connection.set_definition(def_after_creation) # 2nd call to apply additional parameters
            elif exists:
                if args.state == "absent":
                    connection.delete()
                elif current_def != new_def or 0 < len(encrypted_fields["params"]):
                    #for field in encrypted_fields_list:
                        #new_def_value = encrypted_fields.get(field, None)
                        ## TODO: Bugfix about password here
                        #del new_def["params"][field]
                        ##if new_def_value is not None:
                            ##new_def["params"][field] = new_def_value
                        ##else:
                            ##new_def["params"][field] = encrypted_fields_before_change.get(field)
                    result["message"] = str(connection.set_definition(new_def))
                    #if 0 < len(encrypted_fields["params"]):
                        ## Get again the definition to test again the encrypted fields
                        #new_def_after_submit = connection.get_definition()
                        #encrypted_fields_after_change = {"params":{}}
                        #for field in encrypted_fields_list:
                            #value = new_def_after_submit.get(field,None)
                            #if value is not None:
                                #encrypted_fields_after_change["params"][field] = value
                        #if encrypted_fields_before_change != encrypted_fields_after_change:
                            #result["changed"] = True
                            #result["message"] = "MODIFIED"

        module.exit_json(**result)
    except Exception as e:
        module.fail_json(msg="{}: {}".format(pytypefunc(e).__name__,str(e)))

def main():
    run_module()

if __name__ == '__main__':
    main()
