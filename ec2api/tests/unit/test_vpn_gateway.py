# Copyright 2014
# The Cloudscaling Group, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import mock
from neutronclient.common import exceptions as neutron_exception

from ec2api.tests.unit import base
from ec2api.tests.unit import fakes
from ec2api.tests.unit import matchers
from ec2api.tests.unit import tools


class VpnGatewayTestCase(base.ApiTestCase):

    def setUp(self):
        super(VpnGatewayTestCase, self).setUp()
        self.DB_VPN_GATEWAY_2_ATTACHED = tools.update_dict(
            fakes.DB_VPN_GATEWAY_2, {'vpc_id': fakes.ID_EC2_VPC_2})
        self.DB_VPN_GATEWAY_1_DETACHED = tools.update_dict(
            fakes.DB_VPN_GATEWAY_1, {'vpc_id': None})

    def test_create_vpn_gateway(self):
        self.db_api.add_item.side_effect = (
            tools.get_db_api_add_item(fakes.ID_EC2_VPN_GATEWAY_2))

        resp = self.execute('CreateVpnGateway',
                            {'Type': 'ipsec.1'})
        self.assertEqual({'vpnGateway': fakes.EC2_VPN_GATEWAY_2}, resp)
        self.db_api.add_item.assert_called_once_with(
                mock.ANY, 'vgw', {}, project_id=None)

    def test_attach_vpn_gateway(self):
        self.configure(external_network=fakes.NAME_OS_PUBLIC_NETWORK)
        self.set_mock_db_items(fakes.DB_VPN_GATEWAY_1, fakes.DB_VPN_GATEWAY_2,
                               fakes.DB_VPC_2, fakes.DB_IGW_1, fakes.DB_IGW_2)
        self.neutron.list_networks.return_value = (
                {'networks': [{'id': fakes.ID_OS_PUBLIC_NETWORK}]})

        def do_check():
            resp = self.execute('AttachVpnGateway',
                                {'VpcId': fakes.ID_EC2_VPC_2,
                                 'VpnGatewayId': fakes.ID_EC2_VPN_GATEWAY_2})
            self.assertEqual({'attachment': {'state': 'attached',
                                             'vpcId': fakes.ID_EC2_VPC_2}},
                             resp)
            self.db_api.update_item.assert_called_once_with(
                    mock.ANY, self.DB_VPN_GATEWAY_2_ATTACHED)

        do_check()
        self.neutron.add_gateway_router.assert_called_once_with(
                fakes.ID_OS_ROUTER_2,
                {'network_id': fakes.ID_OS_PUBLIC_NETWORK})
        self.neutron.list_networks.assert_called_once_with(
                **{'router:external': True,
                   'name': fakes.NAME_OS_PUBLIC_NETWORK})

        # Internet gateway is already attached
        self.db_api.reset_mock()
        self.neutron.reset_mock()
        igw_2 = tools.update_dict(fakes.DB_IGW_2,
                                  {'vpc_id': fakes.ID_EC2_VPC_2})
        self.add_mock_db_items(igw_2)
        do_check()
        self.assertFalse(self.neutron.add_gateway_router.called)

    def test_attach_vpn_gateway_idempotent(self):
        self.configure(external_network=fakes.NAME_OS_PUBLIC_NETWORK)
        self.set_mock_db_items(fakes.DB_VPN_GATEWAY_1, fakes.DB_VPC_1)

        resp = self.execute('AttachVpnGateway',
                            {'VpcId': fakes.ID_EC2_VPC_1,
                             'VpnGatewayId': fakes.ID_EC2_VPN_GATEWAY_1})
        self.assertEqual({'attachment': {'state': 'attached',
                                         'vpcId': fakes.ID_EC2_VPC_1}},
                         resp)
        self.assertFalse(self.db_api.update_item.called)
        self.assertFalse(self.neutron.add_gateway_router.called)

    def test_attach_vpn_gateway_invalid_parameters(self):
        def do_check(error_code):
            self.assert_execution_error(
                error_code, 'AttachVpnGateway',
                {'VpcId': fakes.ID_EC2_VPC_2,
                 'VpnGatewayId': fakes.ID_EC2_VPN_GATEWAY_2})

            self.assertFalse(self.db_api.update_item.called)
            self.db_api.reset_mock()

        self.set_mock_db_items(fakes.DB_VPC_2)
        do_check('InvalidVpnGatewayID.NotFound')

        self.set_mock_db_items(fakes.DB_VPN_GATEWAY_2)
        do_check('InvalidVpcID.NotFound')

        self.set_mock_db_items(
            tools.update_dict(fakes.DB_VPN_GATEWAY_2,
                              {'vpc_id': fakes.ID_EC2_VPC_1}),
            fakes.DB_VPC_2)
        do_check('VpnGatewayAttachmentLimitExceeded')

        self.set_mock_db_items(
            fakes.DB_VPN_GATEWAY_2, fakes.DB_VPC_2,
            tools.update_dict(fakes.DB_VPN_GATEWAY_1,
                              {'vpc_id': fakes.ID_EC2_VPC_2}))
        do_check('InvalidVpcState')

    @tools.screen_unexpected_exception_logs
    def test_attach_vpn_gateway_rollback(self):
        self.configure(external_network=fakes.NAME_OS_PUBLIC_NETWORK)
        self.set_mock_db_items(fakes.DB_VPN_GATEWAY_1, fakes.DB_VPN_GATEWAY_2,
                               fakes.DB_VPC_2)
        self.neutron.list_networks.return_value = (
                {'networks': [{'id': fakes.ID_OS_PUBLIC_NETWORK}]})
        self.neutron.add_gateway_router.side_effect = Exception()

        self.assert_execution_error(
            self.ANY_EXECUTE_ERROR, 'AttachVpnGateway',
            {'VpcId': fakes.ID_EC2_VPC_2,
             'VpnGatewayId': fakes.ID_EC2_VPN_GATEWAY_2})

        self.db_api.update_item.assert_any_call(
                mock.ANY, fakes.DB_VPN_GATEWAY_2)

    def test_detach_vpn_gateway(self):
        self.set_mock_db_items(fakes.DB_VPN_GATEWAY_1, fakes.DB_VPC_1)

        def do_check():
            resp = self.execute(
                    'DetachVpnGateway',
                    {'VpcId': fakes.ID_EC2_VPC_1,
                     'VpnGatewayId': fakes.ID_EC2_VPN_GATEWAY_1})
            self.assertEqual({'return': True}, resp)
            self.db_api.update_item.assert_called_once_with(
                    mock.ANY, self.DB_VPN_GATEWAY_1_DETACHED)

        do_check()
        self.neutron.remove_gateway_router.assert_called_once_with(
                fakes.ID_OS_ROUTER_1)

        # Internet gateway is still attached
        self.db_api.reset_mock()
        self.neutron.reset_mock()
        self.add_mock_db_items(fakes.DB_IGW_1)
        do_check()
        self.assertFalse(self.neutron.remove_gateway_router.called)

    def test_detach_vpn_gateway_invalid_parameters(self):
        def do_check(error_code):
            self.assert_execution_error(
                error_code, 'DetachVpnGateway',
                {'VpcId': fakes.ID_EC2_VPC_1,
                 'VpnGatewayId': fakes.ID_EC2_VPN_GATEWAY_2})

            self.assertEqual(0, self.neutron.remove_gateway_router.call_count)
            self.assertEqual(0, self.db_api.update_item.call_count)

            self.neutron.reset_mock()
            self.db_api.reset_mock()

        self.set_mock_db_items()
        do_check('InvalidVpnGatewayID.NotFound')

        self.set_mock_db_items(fakes.DB_VPN_GATEWAY_2)
        do_check('InvalidVpnGatewayAttachment.NotFound')

        self.set_mock_db_items(self.DB_VPN_GATEWAY_2_ATTACHED)
        do_check('InvalidVpnGatewayAttachment.NotFound')

    def test_detach_vpn_gateway_no_router(self):
        self.set_mock_db_items(fakes.DB_VPN_GATEWAY_1, fakes.DB_VPC_1)
        self.neutron.remove_gateway_router.side_effect = (
                neutron_exception.NotFound)

        resp = self.execute(
                'DetachVpnGateway',
                {'VpcId': fakes.ID_EC2_VPC_1,
                 'VpnGatewayId': fakes.ID_EC2_VPN_GATEWAY_1})

        self.assertEqual(True, resp['return'])
        self.neutron.remove_gateway_router.assert_called_once_with(
                fakes.ID_OS_ROUTER_1)

    @tools.screen_unexpected_exception_logs
    def test_detach_vpn_gateway_rollback(self):
        self.set_mock_db_items(fakes.DB_VPN_GATEWAY_1, fakes.DB_VPC_1)
        self.neutron.remove_gateway_router.side_effect = Exception()

        self.assert_execution_error(
            self.ANY_EXECUTE_ERROR, 'DetachVpnGateway',
            {'VpcId': fakes.ID_EC2_VPC_1,
             'VpnGatewayId': fakes.ID_EC2_VPN_GATEWAY_1})

        self.db_api.update_item.assert_any_call(
                mock.ANY, fakes.DB_VPN_GATEWAY_1)

    def test_delete_vpn_gateway(self):
        self.set_mock_db_items(fakes.DB_VPN_GATEWAY_2)

        resp = self.execute(
                'DeleteVpnGateway',
                {'VpnGatewayId': fakes.ID_EC2_VPN_GATEWAY_2})

        self.assertEqual({'return': True}, resp)
        self.db_api.delete_item.assert_called_once_with(
            mock.ANY, fakes.ID_EC2_VPN_GATEWAY_2)

    def test_delete_vpn_gateway_invalid_parameters(self):
        def do_check(error_code):
            self.assert_execution_error(
                error_code, 'DeleteVpnGateway',
                {'VpnGatewayId': fakes.ID_EC2_VPN_GATEWAY_1})

            self.assertFalse(self.db_api.delete_item.called)
            self.db_api.reset_mock()

        self.set_mock_db_items()
        do_check('InvalidVpnGatewayID.NotFound')

        self.set_mock_db_items(fakes.DB_VPN_GATEWAY_1)
        do_check('IncorrectState')

    def test_describe_vpn_gateways(self):
        self.set_mock_db_items(fakes.DB_VPN_GATEWAY_1, fakes.DB_VPN_GATEWAY_2)

        resp = self.execute('DescribeVpnGateways', {})
        self.assertThat(resp['vpnGatewaySet'],
                        matchers.ListMatches([fakes.EC2_VPN_GATEWAY_1,
                                              fakes.EC2_VPN_GATEWAY_2]))

        resp = self.execute('DescribeVpnGateways',
                            {'VpnGatewayId.1': fakes.ID_EC2_VPN_GATEWAY_2})
        self.assertThat(resp['vpnGatewaySet'],
                        matchers.ListMatches([fakes.EC2_VPN_GATEWAY_2]))
        self.db_api.get_items_by_ids.assert_called_once_with(
            mock.ANY, set([fakes.ID_EC2_VPN_GATEWAY_2]))

        self.check_filtering(
            'DescribeVpnGateways', 'vpnGatewaySet',
            [('attachment.state', 'attached'),
             ('attachment.vpc-id', fakes.ID_EC2_VPC_1),
             ('state', 'available'),
             ('type', 'ipsec.1'),
             ('vpn-gateway-id', fakes.ID_EC2_VPN_GATEWAY_2)])
        self.check_tag_support(
            'DescribeVpnGateways', 'vpnGatewaySet',
            fakes.ID_EC2_VPN_GATEWAY_2, 'vpnGatewayId')