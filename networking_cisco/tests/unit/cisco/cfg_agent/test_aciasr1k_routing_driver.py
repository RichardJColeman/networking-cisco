# Copyright 2014 Cisco Systems, Inc.  All rights reserved.
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

import sys

import mock
import netaddr
from oslo_config import cfg
from oslo_utils import uuidutils

from networking_cisco.plugins.cisco.cfg_agent.device_drivers.asr1k import (
    aci_asr1k_routing_driver as driver)
from networking_cisco.plugins.cisco.cfg_agent.device_drivers.asr1k import (
    aci_asr1k_snippets as snippets)
from networking_cisco.plugins.cisco.cfg_agent.device_drivers.asr1k import (
    asr1k_snippets as asr_snippets)
from networking_cisco.plugins.cisco.cfg_agent.device_drivers.csr1kv import (
    cisco_csr1kv_snippets as csr_snippets)
from networking_cisco.plugins.cisco.cfg_agent.service_helpers import (
    routing_svc_helper)
from networking_cisco.tests.unit.cisco.cfg_agent import (
    test_asr1k_routing_driver as asr1ktest)
from neutron.common import constants as l3_constants

sys.modules['ncclient'] = mock.MagicMock()

_uuid = uuidutils.generate_uuid
HA_INFO = 'ha_info'
FAKE_ID = _uuid()
PORT_ID = _uuid()


class ASR1kRoutingDriverAci(asr1ktest.ASR1kRoutingDriver):
    def setUp(self):
        super(ASR1kRoutingDriverAci, self).setUp()

        device_params = {'management_ip_address': 'fake_ip',
                         'protocol_port': 22,
                         'credentials': {"user_name": "stack",
                                         "password": "cisco"},
                         'timeout': None,
                         'id': '0000-1',
                         'device_id': 'ASR-1'
                         }
        self.driver = driver.AciASR1kRoutingDriver(**device_params)
        self.driver._ncc_connection = mock.MagicMock()
        self.driver._check_response = mock.MagicMock(return_value=True)
        self.driver._check_acl = mock.MagicMock(return_value=False)
        self.ri_global.router['tenant_id'] = _uuid()
        self.router['tenant_id'] = _uuid()
        self.ri = routing_svc_helper.RouterInfo(FAKE_ID, self.router)
        self.vrf = self.ri.router['tenant_id']
        self.driver._get_vrfs = mock.Mock(return_value=[self.vrf])
        self.transit_gw_ip = '1.103.2.254'
        self.transit_gw_vip = '1.103.2.2'
        self.transit_cidr = '1.103.2.1/24'
        self.transit_vlan = '1035'
        self.int_port = {'id': PORT_ID,
                         'ip_cidr': self.gw_ip_cidr,
                         'fixed_ips': [{'ip_address': self.gw_ip}],
                         'subnets': [{'cidr': self.gw_ip_cidr,
                                      'gateway_ip': self.gw_ip}],
                         'hosting_info': {
                             'physical_interface': self.phy_infc,
                             'segmentation_id': self.transit_vlan,
                             'gateway_ip': self.transit_gw_ip,
                             'cidr_exposed': self.transit_cidr
                         },
                         HA_INFO: self.gw_ha_info}
        self.gw_port = {'id': PORT_ID,
                        'ip_cidr': self.gw_ip_cidr,
                        'fixed_ips': [{'ip_address': self.gw_ip}],
                        'subnets': [{'cidr': self.gw_ip_cidr,
                                     'gateway_ip': self.gw_ip}],
                        'hosting_info': {
                            'physical_interface': self.phy_infc,
                            'segmentation_id': self.vlan_int},
                        HA_INFO: self.gw_ha_info}
        self.port = self.int_port
        int_ports = [self.port]
        self.router[l3_constants.INTERFACE_KEY] = int_ports
        self.ri.internal_ports = int_ports
        self.ri_global.internal_ports = int_ports
        self.TEST_CIDR = '20.0.0.0/24'
        self.TEST_SNAT_ID = _uuid()
        self.ex_gw_port['hosting_info']['snat_subnets'] = [
            {'id': self.TEST_SNAT_ID,
             'cidr': self.TEST_CIDR}
        ]
        net = netaddr.IPNetwork(self.TEST_CIDR)
        self.TEST_CIDR_SNAT_IP = str(netaddr.IPAddress(net.first + 2))
        self.TEST_CIDR_SECONDARY_IP = str(netaddr.IPAddress(net.last - 1))

    def _assert_edit_run_cfg_calls(self, call_args):
        mock_calls = self.driver._ncc_connection.edit_config.mock_calls
        for index in range(len(call_args)):
            snippet_name, params = call_args[index]
            if params:
                confstr = snippet_name % params
            else:
                confstr = snippet_name
            self.assertEqual(
                mock_calls[index][2]['config'],
                confstr)

    def test_internal_network_added(self):
        cfg.CONF.set_override('enable_multi_region', False, 'multi_region')
        self.driver.internal_network_added(self.ri, self.port)
        sub_interface = self.phy_infc + '.' + str(self.transit_vlan)
        net = netaddr.IPNetwork(self.gw_ip_cidr).network
        mask = netaddr.IPNetwork(self.gw_ip_cidr).netmask
        cfg_args_route = (self.vrf, net, mask, sub_interface,
            self.transit_gw_ip)
        self.assert_edit_run_cfg(
            snippets.SET_TENANT_ROUTE_WITH_INTF, cfg_args_route)

        sub_interface = self.phy_infc + '.' + str(self.transit_vlan)
        mask = netaddr.IPNetwork(self.transit_cidr).netmask
        cfg_args_sub = (sub_interface, self.transit_vlan, self.vrf,
                        self.transit_cidr.split("/")[0], mask)
        self.assert_edit_run_cfg(
            asr_snippets.CREATE_SUBINTERFACE_WITH_ID, cfg_args_sub)

        cfg_args_hsrp = self._generate_hsrp_cfg_args(
            sub_interface, self.gw_ha_group,
            self.ha_priority, self.transit_gw_vip,
            self.transit_vlan)
        self.assert_edit_run_cfg(
            asr_snippets.SET_INTC_ASR_HSRP_EXTERNAL, cfg_args_hsrp)

    def test_internal_network_added_with_multi_region(self):
        cfg.CONF.set_override('enable_multi_region', True, 'multi_region')
        is_multi_region_enabled = cfg.CONF.multi_region.enable_multi_region
        self.assertEqual(True, is_multi_region_enabled)

        region_id = cfg.CONF.multi_region.region_id

        vrf = self.vrf + "-" + region_id

        self.driver.internal_network_added(self.ri, self.port)

        sub_interface = self.phy_infc + '.' + str(self.transit_vlan)
        net = netaddr.IPNetwork(self.gw_ip_cidr).network
        mask = netaddr.IPNetwork(self.gw_ip_cidr).netmask
        cfg_args_route = (vrf, net, mask, sub_interface,
            self.transit_gw_ip)
        self.assert_edit_run_cfg(
            snippets.SET_TENANT_ROUTE_WITH_INTF, cfg_args_route)

        sub_interface = self.phy_infc + '.' + str(self.transit_vlan)
        mask = netaddr.IPNetwork(self.transit_cidr).netmask
        cfg_args_sub = (sub_interface, region_id, self.transit_vlan, vrf,
                        self.transit_cidr.split("/")[0], mask)
        self.assert_edit_run_cfg(
            asr_snippets.CREATE_SUBINTERFACE_REGION_ID_WITH_ID, cfg_args_sub)

        cfg_args_hsrp = self._generate_hsrp_cfg_args(
            sub_interface, self.gw_ha_group,
            self.ha_priority, self.transit_gw_vip,
            self.transit_vlan)
        self.assert_edit_run_cfg(
            asr_snippets.SET_INTC_ASR_HSRP_EXTERNAL, cfg_args_hsrp)

        cfg.CONF.set_override('enable_multi_region', False, 'multi_region')

    def test_internal_network_added_global_router(self):
        self.port = self.gw_port
        super(ASR1kRoutingDriverAci,
            self).test_internal_network_added_global_router()
        self.port = self.int_port

    def test_internal_network_added_global_router_with_multi_region(self):
        self.port = self.gw_port
        super(ASR1kRoutingDriverAci,
            self).test_internal_network_added_global_router_with_multi_region()
        self.port = self.int_port

    def test_driver_enable_internal_network_NAT(self):
        self.port = self.gw_port
        super(ASR1kRoutingDriverAci,
            self).test_driver_enable_internal_network_NAT()
        self.port = self.int_port

    def test_driver_enable_internal_network_NAT_with_multi_region(self):
        self.port = self.gw_port
        super(ASR1kRoutingDriverAci,
            self).test_driver_enable_internal_network_NAT_with_multi_region()
        self.port = self.int_port

    def test_driver_disable_internal_network_NAT_with_multi_region(self):
        self.port = self.gw_port
        super(ASR1kRoutingDriverAci,
            self).test_driver_disable_internal_network_NAT_with_multi_region()
        self.port = self.int_port

    def test_driver_disable_internal_network_NAT(self):
        self.port = self.gw_port
        super(ASR1kRoutingDriverAci,
            self).test_driver_disable_internal_network_NAT()
        self.port = self.int_port

    def test_internal_network_removed(self):
        self.driver._do_remove_sub_interface = mock.MagicMock()
        self.driver.internal_network_removed(self.ri, self.port)
        self.assertFalse(self.driver._do_remove_sub_interface.called)

    def _next_ip(self, curr_ip):
        ip = netaddr.IPAddress(curr_ip)
        ip.value += 1
        return str(ip)

    def test_floating_ip_added_extra_subnets(self):

        cfg.CONF.set_override('enable_multi_region', False, 'multi_region')
        self.ex_gw_port['extra_subnets'] = [{'cidr': self.TEST_CIDR}]
        self.driver.floating_ip_added(self.ri, self.ex_gw_port,
                                      self.floating_ip, self.fixed_ip)
        self.driver.floating_ip_added(self.ri, self.ex_gw_port,
                                      self._next_ip(self.floating_ip),
                                      self._next_ip(self.fixed_ip))
        self._assert_number_of_edit_run_cfg_calls(4)
        call_args = []
        call_args.append((asr_snippets.SET_STATIC_SRC_TRL_NO_VRF_MATCH,
            (self.fixed_ip, self.floating_ip, self.vrf,
            self.ex_gw_ha_group, self.vlan_ext)))
        sub_interface = self.phy_infc + '.' + str(self.vlan_ext)
        call_args.append((snippets.ADD_SECONDARY_IP,
            (sub_interface, self.TEST_CIDR_SECONDARY_IP, '255.255.255.0')))
        call_args.append((asr_snippets.SET_STATIC_SRC_TRL_NO_VRF_MATCH,
            (self._next_ip(self.fixed_ip), self._next_ip(self.floating_ip),
             self.vrf, self.ex_gw_ha_group, self.vlan_ext)))
        call_args.append((snippets.ADD_SECONDARY_IP,
            (sub_interface, self.TEST_CIDR_SECONDARY_IP, '255.255.255.0')))
        self._assert_edit_run_cfg_calls(call_args)
        del(self.ex_gw_port['extra_subnets'])

    def test_floating_ip_removed_extra_subnets(self):
        cfg.CONF.set_override('enable_multi_region', False, 'multi_region')
        self.ex_gw_port['extra_subnets'] = [{'cidr': self.TEST_CIDR}]
        fips = [{'floating_ip_address': self.floating_ip},
                {'floating_ip_address': self._next_ip(self.floating_ip)}]
        self.router[l3_constants.FLOATINGIP_KEY] = fips
        # First removal shouldn't remove secondary address
        self.driver.floating_ip_removed(self.ri, self.ex_gw_port,
                                        self._next_ip(self.floating_ip),
                                        self._next_ip(self.fixed_ip))
        self._assert_number_of_edit_run_cfg_calls(1)
        call_args = []
        call_args.append((asr_snippets.REMOVE_STATIC_SRC_TRL_NO_VRF_MATCH,
            (self._next_ip(self.fixed_ip), self._next_ip(self.floating_ip),
             self.vrf, self.ex_gw_ha_group, self.vlan_ext)))
        self._assert_edit_run_cfg_calls(call_args)
        self.driver._ncc_connection.edit_config.reset_mock()
        # adjust our list for the next call
        self.router[l3_constants.FLOATINGIP_KEY].pop()
        self.driver.floating_ip_removed(self.ri, self.ex_gw_port,
                                        self.floating_ip, self.fixed_ip)
        self._assert_number_of_edit_run_cfg_calls(2)
        call_args = []
        call_args.append((asr_snippets.REMOVE_STATIC_SRC_TRL_NO_VRF_MATCH,
            (self.fixed_ip, self.floating_ip, self.vrf,
             self.ex_gw_ha_group, self.vlan_ext)))
        sub_interface = self.phy_infc + '.' + str(self.vlan_ext)
        call_args.append((snippets.REMOVE_SECONDARY_IP,
            (sub_interface, self.TEST_CIDR_SECONDARY_IP, '255.255.255.0')))
        self._assert_edit_run_cfg_calls(call_args)
        del(self.ex_gw_port['extra_subnets'])

    def test_external_network_added_user_visible_router_nat_pool(self):
        cfg.CONF.set_override('enable_multi_region', False, 'multi_region')
        self.ex_gw_port['extra_subnets'] = [{'cidr': self.TEST_CIDR,
                                             'id': self.TEST_SNAT_ID}]
        self.driver.external_gateway_added(self.ri, self.ex_gw_port)

        sub_interface = self.phy_infc + '.' + str(self.vlan_ext)
        self.assert_edit_run_cfg(csr_snippets.ENABLE_INTF, sub_interface)

        net = netaddr.IPNetwork(self.TEST_CIDR)
        cfg_params_nat = (self.vrf + '_nat_pool', self.TEST_CIDR_SNAT_IP,
                          self.TEST_CIDR_SNAT_IP, net.netmask)
        self.assert_edit_run_cfg(asr_snippets.CREATE_NAT_POOL, cfg_params_nat)
        del(self.ex_gw_port['extra_subnets'])

    def test_external_gateway_removed_user_visible_router_nat_pool(self):
        cfg.CONF.set_override('enable_multi_region', False, 'multi_region')
        self.ex_gw_port['extra_subnets'] = [{'cidr': self.TEST_CIDR,
                                             'id': self.TEST_SNAT_ID}]
        self.driver.external_gateway_removed(self.ri, self.ex_gw_port)

        net = netaddr.IPNetwork(self.TEST_CIDR)
        cfg_params_nat = (self.vrf + '_nat_pool', self.TEST_CIDR_SNAT_IP,
                          self.TEST_CIDR_SNAT_IP, net.netmask)
        self.assert_edit_run_cfg(asr_snippets.DELETE_NAT_POOL, cfg_params_nat)

        sub_interface = self.phy_infc + '.' + str(self.vlan_ext)
        cfg_params_remove_route = (self.vrf,
                                   sub_interface, self.ex_gw_gateway_ip)
        self.assert_edit_run_cfg(asr_snippets.REMOVE_DEFAULT_ROUTE_WITH_INTF,
                                 cfg_params_remove_route)
        del(self.ex_gw_port['extra_subnets'])

    def test_external_gateway_removed_non_ha(self):
        cfg.CONF.set_override('enable_multi_region', False, 'multi_region')
        self._make_test_router_non_ha()
        self.driver.external_gateway_removed(self.ri, self.ex_gw_port)

        cfg_params_nat = (self.vrf + '_nat_pool', self.TEST_CIDR_SNAT_IP,
                          self.TEST_CIDR_SNAT_IP, self.ex_gw_ip_mask)
        self.assert_edit_run_cfg(asr_snippets.DELETE_NAT_POOL, cfg_params_nat)

        sub_interface = self.phy_infc + '.' + str(self.vlan_ext)
        cfg_params_remove_route = (self.vrf,
                                   sub_interface, self.ex_gw_gateway_ip)
        self.assert_edit_run_cfg(asr_snippets.REMOVE_DEFAULT_ROUTE_WITH_INTF,
                                 cfg_params_remove_route)

    def test_external_gateway_removed(self):
        cfg.CONF.set_override('enable_multi_region', False, 'multi_region')
        self.driver.external_gateway_removed(self.ri, self.ex_gw_port)

        cfg_params_nat = (self.vrf + '_nat_pool', self.TEST_CIDR_SNAT_IP,
                          self.TEST_CIDR_SNAT_IP, self.ex_gw_ip_mask)
        self.assert_edit_run_cfg(asr_snippets.DELETE_NAT_POOL, cfg_params_nat)

        sub_interface = self.phy_infc + '.' + str(self.vlan_ext)
        cfg_params_remove_route = (self.vrf,
                                   sub_interface, self.ex_gw_gateway_ip)
        self.assert_edit_run_cfg(asr_snippets.REMOVE_DEFAULT_ROUTE_WITH_INTF,
                                 cfg_params_remove_route)

    def test_external_gateway_removed_with_multi_region(self):
        cfg.CONF.set_override('enable_multi_region', True, 'multi_region')
        is_multi_region_enabled = cfg.CONF.multi_region.enable_multi_region
        self.assertEqual(True, is_multi_region_enabled)
        region_id = cfg.CONF.multi_region.region_id
        vrf = self.vrf + "-" + region_id

        self.driver.external_gateway_removed(self.ri, self.ex_gw_port)

        cfg_params_nat = (vrf + '_nat_pool', self.TEST_CIDR_SNAT_IP,
                          self.TEST_CIDR_SNAT_IP, self.ex_gw_ip_mask)
        self.assert_edit_run_cfg(asr_snippets.DELETE_NAT_POOL, cfg_params_nat)

        sub_interface = self.phy_infc + '.' + str(self.vlan_ext)
        cfg_params_remove_route = (vrf,
                                   sub_interface, self.ex_gw_gateway_ip)
        self.assert_edit_run_cfg(asr_snippets.REMOVE_DEFAULT_ROUTE_WITH_INTF,
                                 cfg_params_remove_route)
        cfg.CONF.set_override('enable_multi_region', False, 'multi_region')

    def test_external_network_added_redundancy_router(self):
        cfg.CONF.set_override('enable_multi_region', False, 'multi_region')
        self._make_test_router_redundancy_router()
        self.driver.external_gateway_added(self.ri, self.ex_gw_port)

        sub_interface = self.phy_infc + '.' + str(self.vlan_ext)
        self.assert_edit_run_cfg(csr_snippets.ENABLE_INTF, sub_interface)
        cfg_params_nat = (self.vrf + '_nat_pool', self.TEST_CIDR_SNAT_IP,
                          self.TEST_CIDR_SNAT_IP, self.ex_gw_ip_mask)
        self.assert_edit_run_cfg(asr_snippets.CREATE_NAT_POOL, cfg_params_nat)

    def test_external_network_added_user_visible_router(self):
        cfg.CONF.set_override('enable_multi_region', False, 'multi_region')
        self.driver.external_gateway_added(self.ri, self.ex_gw_port)

        sub_interface = self.phy_infc + '.' + str(self.vlan_ext)
        self.assert_edit_run_cfg(csr_snippets.ENABLE_INTF, sub_interface)

        cfg_params_nat = (self.vrf + '_nat_pool', self.TEST_CIDR_SNAT_IP,
                          self.TEST_CIDR_SNAT_IP, self.ex_gw_ip_mask)
        self.assert_edit_run_cfg(asr_snippets.CREATE_NAT_POOL, cfg_params_nat)

    def test_external_gateway_removed_redundancy_router(self):
        cfg.CONF.set_override('enable_multi_region', False, 'multi_region')
        self._make_test_router_redundancy_router()
        self.driver.external_gateway_removed(self.ri, self.ex_gw_port)

        cfg_params_nat = (self.vrf + '_nat_pool', self.TEST_CIDR_SNAT_IP,
                          self.TEST_CIDR_SNAT_IP, self.ex_gw_ip_mask)
        self.assert_edit_run_cfg(asr_snippets.DELETE_NAT_POOL, cfg_params_nat)

        sub_interface = self.phy_infc + '.' + str(self.vlan_ext)
        cfg_params_remove_route = (self.vrf,
                                   sub_interface, self.ex_gw_gateway_ip)
        self.assert_edit_run_cfg(asr_snippets.REMOVE_DEFAULT_ROUTE_WITH_INTF,
                                 cfg_params_remove_route)

    def test_external_network_added_with_multi_region(self):
        cfg.CONF.set_override('enable_multi_region', True, 'multi_region')
        is_multi_region_enabled = cfg.CONF.multi_region.enable_multi_region
        self.assertEqual(True, is_multi_region_enabled)
        region_id = cfg.CONF.multi_region.region_id
        vrf = self.vrf + "-" + region_id
        self.driver.external_gateway_added(self.ri, self.ex_gw_port)

        sub_interface = self.phy_infc + '.' + str(self.vlan_ext)
        self.assert_edit_run_cfg(csr_snippets.ENABLE_INTF, sub_interface)

        cfg_params_nat = (vrf + '_nat_pool', self.TEST_CIDR_SNAT_IP,
                          self.TEST_CIDR_SNAT_IP, self.ex_gw_ip_mask)
        self.assert_edit_run_cfg(asr_snippets.CREATE_NAT_POOL, cfg_params_nat)
        cfg.CONF.set_override('enable_multi_region', False, 'multi_region')
        cfg_params_nat = (vrf + '_nat_pool', self.TEST_CIDR_SNAT_IP,
                          self.TEST_CIDR_SNAT_IP, self.ex_gw_ip_mask)
        self.assert_edit_run_cfg(asr_snippets.CREATE_NAT_POOL, cfg_params_nat)
        cfg.CONF.set_override('enable_multi_region', False, 'multi_region')

    def test_external_gateway_removed_user_visible_router(self):
        cfg.CONF.set_override('enable_multi_region', False, 'multi_region')
        self.driver.external_gateway_removed(self.ri, self.ex_gw_port)

        cfg_params_nat = (self.vrf + '_nat_pool', self.TEST_CIDR_SNAT_IP,
                          self.TEST_CIDR_SNAT_IP, self.ex_gw_ip_mask)
        self.assert_edit_run_cfg(asr_snippets.DELETE_NAT_POOL, cfg_params_nat)

        sub_interface = self.phy_infc + '.' + str(self.vlan_ext)
        cfg_params_remove_route = (self.vrf,
                                   sub_interface, self.ex_gw_gateway_ip)
        self.assert_edit_run_cfg(asr_snippets.REMOVE_DEFAULT_ROUTE_WITH_INTF,
                                 cfg_params_remove_route)

    def test_external_network_added_non_ha(self):
        cfg.CONF.set_override('enable_multi_region', False, 'multi_region')
        self._make_test_router_non_ha()
        self.driver.external_gateway_added(self.ri, self.ex_gw_port)

        sub_interface = self.phy_infc + '.' + str(self.vlan_ext)
        self.assert_edit_run_cfg(csr_snippets.ENABLE_INTF, sub_interface)

        cfg_params_nat = (self.vrf + '_nat_pool', self.TEST_CIDR_SNAT_IP,
                          self.TEST_CIDR_SNAT_IP, self.ex_gw_ip_mask)
        self.assert_edit_run_cfg(asr_snippets.CREATE_NAT_POOL, cfg_params_nat)
