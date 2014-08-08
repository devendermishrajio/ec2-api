#    Copyright 2014 Cloudscaling Group, Inc
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.


import netaddr
from oslo.config import cfg

from ec2api.api import clients
from ec2api.api import ec2utils
from ec2api.api import utils
from ec2api.db import api as db_api
from ec2api import exception
from ec2api.openstack.common import log as logging


CONF = cfg.CONF
LOG = logging.getLogger(__name__)


"""DHCP options related API implementation
"""

DHCP_OPTIONS_MAP = {'domain-name-servers': 'dns-servers',
                    'domain-name': 'domain-name',
                    'ntp-servers': 'ntp-server',
                    'netbios-name-servers': 'netbios-ns',
                    'netbios-node-type': 'netbios-nodetype'}


def create_dhcp_options(context, dhcp_configuration):
    dhcp_options = {}
    for dhcp_option in dhcp_configuration:
        key = dhcp_option['key']
        value = dhcp_option['value']
        if key not in DHCP_OPTIONS_MAP:
            raise exception.InvalidParameterValue(
                        value=value,
                        parameter=key,
                        reason='Unrecognized key is specified')
        if not type(value) is dict:
            raise exception.InvalidParameterValue(
                value=value,
                parameter=key,
                reason='List of values is expected')
        if key not in ['domain-name', 'netbios-node-type']:
            ips = []
            for ip in value.values():
                ip_address = netaddr.IPAddress(ip)
                if not ip_address:
                    raise exception.InvalidParameterValue(
                        value=ip,
                        parameter=key,
                        reason='Invalid list of IPs is specified')
                ips.append(ip)
            dhcp_options[key] = ips
        else:
            dhcp_options[key] = [value['1']]
    dhcp_options = db_api.add_item(context, 'dopt',
                                   {'dhcp_configuration': dhcp_options})
    return {'dhcpOptions':
                _format_dhcp_options(context, dhcp_options)}


def delete_dhcp_options(context, dhcp_options_id):
    dhcp_options = ec2utils.get_db_item(context, 'dopt',
                                        dhcp_options_id)
    vpcs = db_api.get_items(context, 'vpc')
    for vpc in vpcs:
        if dhcp_options['id'] == vpc['dhcp_options_id']:
            raise exception.DependencyViolation(
                        obj1_id=ec2utils.get_ec2_id(dhcp_options['id'],
                                                    'dopt'),
                        obj2_id=ec2utils.get_ec2_id(vpc['id'], 'vpc'))
    db_api.delete_item(context, dhcp_options['id'])
    return True


def describe_dhcp_options(context, dhcp_options_id=None,
                          filter=None):
    # TODO(Alex): implement filters
    dhcp_options = ec2utils.get_db_items(context, 'dopt', dhcp_options_id)
    formatted_dhcp_options = []
    for dhcp_opts in dhcp_options:
        formatted_dhcp_options.append(
                _format_dhcp_options(
                        context, dhcp_opts))
    return {'dhcpOptionsSet': formatted_dhcp_options}


def associate_dhcp_options(context, dhcp_options_id, vpc_id):
    vpc = ec2utils.get_db_item(context, 'vpc', vpc_id)
    rollback_dhcp_options_id = vpc.get('dhcp_options_id')
    if dhcp_options_id == 'default':
        dhcp_options_id = None
        dhcp_options = None
    else:
        dhcp_options = ec2utils.get_db_item(context, 'dopt', dhcp_options_id)
        dhcp_options_id = dhcp_options['id']
    neutron = clients.neutron(context)
    os_ports = neutron.list_ports()['ports']
    network_interfaces = db_api.get_items(context, 'eni')
    rollback_dhcp_options_object = (
            db_api.get_item_by_id(context, 'dopt', rollback_dhcp_options_id)
            if dhcp_options_id is not None else
            None)
    with utils.OnCrashCleaner() as cleaner:
        _associate_vpc_item(context, vpc, dhcp_options_id)
        cleaner.addCleanup(_associate_vpc_item, context, vpc,
                           rollback_dhcp_options_id)
        for network_interface in network_interfaces:
            os_port = next((p for p in os_ports
                            if p['id'] == network_interface['os_id']), None)
            if not os_port:
                continue
            _add_dhcp_opts_to_port(context, dhcp_options,
                                   network_interface, os_port, neutron)
            cleaner.addCleanup(_add_dhcp_opts_to_port, context,
                               rollback_dhcp_options_object, network_interface,
                               os_port, neutron)
    return True


def _add_dhcp_opts_to_port(context, dhcp_options, network_interface, os_port,
                           neutron=None):
    dhcp_opts = []
    if dhcp_options is not None:
        for key, value in dhcp_options['dhcp_configuration'].items():
            dhcp_opts.append({'opt_name': DHCP_OPTIONS_MAP[key],
                              'opt_value': ','.join(value)})
    if not neutron:
        neutron = clients.neutron(context)
    neutron.update_port(os_port['id'],
                        {'port': {'extra_dhcp_opts': dhcp_opts}})


def _format_dhcp_options(context, dhcp_options):
    dhcp_configuration = []
    for key, values in dhcp_options['dhcp_configuration'].items():
        items = [{'value': v} for v in values]
        dhcp_configuration.append({'key': key, 'valueSet': items})
    return {'dhcpOptionsId': ec2utils.get_ec2_id(dhcp_options['id'], 'dopt'),
            'dhcpConfigurationSet': dhcp_configuration}


def _associate_vpc_item(context, vpc, dhcp_options_id):
    vpc['dhcp_options_id'] = dhcp_options_id
    db_api.update_item(context, vpc)
