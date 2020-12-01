# -*- coding: utf-8 -*-
#!/usr/bin/env python3

import socket
from . import stix1_mapping
from base64 import b64encode
from collections import defaultdict
from cybox.core import Object, Observable, ObservableComposition, RelatedObject
from cybox.common import Hash, HashList, ByteRun, ByteRuns
from cybox.common.hashes import _set_hash_type
from cybox.common.object_properties import CustomProperties,  Property
from cybox.objects.account_object import Account, Authentication, StructuredAuthenticationMechanism
from cybox.objects.address_object import Address
from cybox.objects.artifact_object import Artifact, RawArtifact
from cybox.objects.as_object import AutonomousSystem
from cybox.objects.custom_object import Custom
from cybox.objects.domain_name_object import DomainName
from cybox.objects.email_message_object import EmailMessage, EmailHeader, EmailRecipients, Attachments
from cybox.objects.file_object import File
from cybox.objects.hostname_object import Hostname
from cybox.objects.http_session_object import HTTPClientRequest, HTTPRequestHeader, HTTPRequestHeaderFields, HTTPRequestLine, HTTPRequestResponse, HTTPSession
from cybox.objects.mutex_object import Mutex
from cybox.objects.network_connection_object import NetworkConnection
from cybox.objects.network_socket_object import NetworkSocket
from cybox.objects.pipe_object import Pipe
from cybox.objects.port_object import Port
from cybox.objects.process_object import ChildPIDList, ImageInfo, PortList, Process
from cybox.objects.socket_address_object import SocketAddress
from cybox.objects.system_object import System, NetworkInterface, NetworkInterfaceList
from cybox.objects.unix_user_account_object import UnixUserAccount
from cybox.objects.uri_object import URI
from cybox.objects.user_account_object import UserAccount
from cybox.objects.whois_object import WhoisEntry, WhoisRegistrants, WhoisRegistrant, WhoisRegistrar, WhoisNameservers
from cybox.objects.win_executable_file_object import WinExecutableFile, PEHeaders, PEFileHeader, PESectionList, PESection, PESectionHeaderStruct, Entropy
from cybox.objects.win_registry_key_object import RegistryValue, RegistryValues, WinRegistryKey
from cybox.objects.win_service_object import WinService
from cybox.objects.win_user_account_object import WinUser
from cybox.objects.x509_certificate_object import X509Certificate, X509CertificateSignature, X509Cert, SubjectPublicKey, RSAPublicKey, Validity
from cybox.utils import Namespace
from datetime import datetime
from mixbox import idgen
from pymisp import MISPAttribute, MISPEvent, MISPObject
from stix.coa import CourseOfAction
from stix.common import InformationSource, Identity, ToolInformation
from stix.common.confidence import Confidence
from stix.common.related import RelatedCOA, RelatedIndicator, RelatedObservable, RelatedThreatActor, RelatedTTP
from stix.common.vocabs import IncidentStatus
from stix.core import STIXPackage, STIXHeader
from stix.data_marking import Marking, MarkingSpecification
from stix.exploit_target import ExploitTarget, Vulnerability, Weakness
from stix.exploit_target.vulnerability import CVSSVector
from stix.extensions.identity.ciq_identity_3_0 import CIQIdentity3_0Instance, STIXCIQIdentity3_0, PartyName, ElectronicAddressIdentifier, FreeTextAddress
from stix.extensions.identity.ciq_identity_3_0 import Address as ciq_Address
from stix.extensions.marking.simple_marking import SimpleMarkingStructure
from stix.extensions.marking.tlp import TLPMarkingStructure
from stix.extensions.test_mechanism.snort_test_mechanism import SnortTestMechanism
from stix.extensions.test_mechanism.yara_test_mechanism import YaraTestMechanism
from stix.incident import Incident, Time, ExternalID, AffectedAsset, AttributedThreatActors, COATaken
from stix.incident.history import History, HistoryItem
from stix.indicator import Indicator
from stix.indicator.valid_time import ValidTime
from stix.threat_actor import ThreatActor
from stix.ttp import TTP, Behavior
from stix.ttp.attack_pattern import AttackPattern
from stix.ttp.malware_instance import MalwareInstance
from stix.ttp.resource import Resource, Tools
from typing import List, Optional, Union

_OBSERVABLE_OBJECT_TYPES = Union[
    Address, Artifact, AutonomousSystem, Custom, DomainName, EmailMessage,
    File, Hostname, HTTPSession, Mutex, Pipe, Port, SocketAddress, System,
    URI, WinRegistryKey, WinService, X509Certificate
]


class MISPtoSTIX1Parser():
    def __init__(self, namespace: str, orgname: str):
        self.namespace = namespace
        self.orgname = orgname
        self.errors = []
        self.warnings = set()
        self.header_comment = []
        self.misp_event = MISPEvent()
        self.objects_to_parse = defaultdict(dict)
        self.contextualised_data = defaultdict(dict)
        self.courses_of_action = {}
        self.threat_actors = {}
        self.ttps = {}
        self.ttp_references = {}

    def parse_misp_event(self, misp_event: dict, version: str):
        self.misp_event.from_dict(**misp_event)
        self._stix_package = self._create_stix_package(version)
        self.incident = self._create_incident()
        self._generate_stix_objects()
        if 'course_of_action' in self.contextualised_data:
            for course_of_action in self.contextualised_data['course_of_action'].values():
                self.incident.add_coa_taken(course_of_action)
        if 'threat_actor' in self.contextualised_data:
            self.incident.attributed_threat_actors = AttributedThreatActors()
            for threat_actor in self.contextualised_data['threat_actor'].values():
                self.incident.attributed_threat_actors.append(threat_actor)
        if 'ttp' in self.contextualised_data:
            for ttp in self.contextualised_data['ttp'].values():
                self.incident.add_leveraged_ttps(ttp)
        # for uuid, ttp in self.ttps.items():
        #     self.parse_ttp_references(uuid, ttp)
        #     self._stix_package.add_ttp(ttp)
        for course_of_action in self.courses_of_action.values():
            self._stix_package.add_course_of_action(course_of_action)
        for threat_actor in self.threat_actors.values():
            self._stix_package.add_threat_actor(threat_actor)
        for ttp in self.ttps.values():
            self._stix_package.add_ttp(ttp)
        self._stix_package.add_incident(self.incident)
        stix_header = STIXHeader()
        stix_header.title = f"Export from {self.namespace} MISP"
        if self.header_comment and len(self.header_comment) == 1:
            stix_header.description = self.header_comment[0]
        self._stix_package.stix_header = stix_header

    @property
    def stix_package(self) -> STIXPackage:
        return self._stix_package

    ################################################################################
    #                     MAIN STIX PACKAGE CREATION FUNCTIONS                     #
    ################################################################################

    def _create_incident(self) -> Incident:
        incident_id = f'{self.orgname}:Incident-{self.misp_event.uuid}'
        incident = Incident(
            id_=incident_id,
            title=self.misp_event.info,
            timestamp=self.misp_event.timestamp
        )
        incident_time = Time()
        incident_time.incident_discovery = self._from_datetime_to_str(self.misp_event.date)
        if self.misp_event.published:
            incident_time.incident_reported = self.misp_event.publish_timestamp
        incident.time = incident_time
        return incident

    def _create_stix_package(self, version: str) -> STIXPackage:
        package_id = f'{self.orgname}:STIXPackage-{self.misp_event.uuid}'
        timestamp = self.misp_event.timestamp
        stix_package = STIXPackage(id_=package_id, timestamp=timestamp)
        stix_package.version = version
        return stix_package

    def _generate_stix_objects(self):
        if hasattr(self.misp_event, 'threat_level_id'):
            threat_level = stix1_mapping.threat_level_mapping[self.misp_event.threat_level_id]
            self._add_journal_entry(f'Event Threat Level: {threat_level}')
        tags = self._handle_tags_and_galaxies()
        if tags:
            self.incident.handling = self._set_handling(tags)
        if hasattr(self.misp_event, 'id'):
            external_id = ExternalID(value=self.misp_event.id, source='MISP Event')
            self.incident.add_external_id(external_id)
        if hasattr(self.misp_event, 'analysis'):
            status = stix1_mapping.status_mapping[self.misp_event.analysis]
            self.incident.status = IncidentStatus(status)
        self.orgc_name = self._set_creator()
        self.incident.information_source = self._set_source()
        self.incident.reporter = self._set_reporter()
        if self.misp_event.get('Attribute'):
            self._resolve_attributes()
        if self.misp_event.get('Object'):
            self._resolve_objects()

    def _handle_tags_and_galaxies(self):
        if self.misp_event.get('Galaxy'):
            tag_names = []
            for galaxy in self.misp_event['Galaxy']:
                galaxy_type = galaxy['type']
                if galaxy_type in stix1_mapping.galaxy_types_mapping:
                    to_call = stix1_mapping.galaxy_types_mapping[galaxy_type]
                    getattr(self, to_call.format('event'))(galaxy)
                    tag_names.extend(self._quick_fetch_tag_names(galaxy))
                else:
                    self.warnings.add(f'{galaxy_type} galaxy in event not mapped.')
            return tuple(tag.name for tag in self.misp_event.tags if tag.name not in tag_names)
        return tuple(tag.name for tag in self.misp_event.tags)

    ################################################################################
    #                         ATTRIBUTES PARSING FUNCTIONS                         #
    ################################################################################

    def _resolve_attributes(self):
        for attribute in self.misp_event.attributes:
            attribute_type = attribute.type
            try:
                if attribute_type in stix1_mapping.attribute_types_mapping:
                    getattr(self, stix1_mapping.attribute_types_mapping[attribute_type])(attribute)
                else:
                    self._parse_custom_attribute(attribute)
                    self.warnings.add(f'MISP Attribute type {attribute_type} not mapped.')
            except Exception:
                self.errors.append(f'Error with the {attribute_type} attribute: {attribute.value}.')

    def _handle_attribute(self, attribute: MISPAttribute, observable: Observable):
        if attribute.to_ids:
            indicator = self._create_indicator_from_attribute(attribute)
            indicator.add_indicator_type(self._set_indicator_type(attribute.type))
            indicator.add_valid_time_position(ValidTime())
            indicator.add_observable(observable)
            tags = self._handle_attribute_tags_and_galaxies(attribute, indicator)
            if tags:
                indicator.handling = self._set_handling(tags)
            related_indicator = RelatedIndicator(
                indicator,
                relationship=attribute.category
            )
            self.incident.related_indicators.append(related_indicator)
        else:
            related_observable = RelatedObservable(
                observable,
                relationship=attribute.category
            )
            self.incident.related_observables.append(related_observable)

    def _handle_attribute_tags_and_galaxies(self, attribute: MISPAttribute, indicator: Indicator) -> tuple:
        if attribute.get('Galaxy'):
            tag_names = []
            for galaxy in attribute['Galaxy']:
                galaxy_type = galaxy['type']
                if galaxy_type in stix1_mapping.galaxy_types_mapping:
                    to_call = stix1_mapping.galaxy_types_mapping[galaxy_type]
                    getattr(self, to_call.format('attribute'))(galaxy, indicator)
                    tag_names.extend(self._quick_fetch_tag_names(galaxy))
                else:
                    self.warnings.add(f'{galaxy_type} galaxy in {attribute.type} attribute not mapped.')
            return tuple(tag.name for tag in attribute.tags if tag.name not in tag_names)
        return tuple(tag.name for tag in attribute.tags)

    def _parse_attachment(self, attribute: MISPAttribute):
        if attribute.data:
            artifact_object = self._create_artifact_object(self._get_b64encoded(attribute.data))
            observable = self._create_observable(artifact_object, attribute.uuid, 'Artifact')
            observable.title = attribute.value
            self._handle_attribute(attribute, observable)
        else:
            self._parse_file_attribute(attribute)

    def _parse_autonomous_system_attribute(self, attribute: MISPAttribute):
        autonomous_system = self._create_autonomous_system_object(attribute.value)
        observable = self._create_observable(autonomous_system, attribute.uuid, 'AS')
        self._handle_attribute(attribute, observable)

    def _parse_custom_attribute(self, attribute: MISPAttribute):
        custom_object = Custom()
        custom_object.custom_properties = CustomProperties()
        property = Property()
        property.name = attribute.type
        property.value = attribute.value
        custom_object.custom_properties.append(property)
        observable = self._create_observable(custom_object, attribute.uuid, 'Custom')
        self._handle_attribute(attribute, observable)

    def _parse_domain_attribute(self, attribute: MISPAttribute):
        domain_object = self._create_domain_object(attribute.value)
        observable = self._create_observable(domain_object, attribute.uuid, 'DomainName')
        self._handle_attribute(attribute, observable)

    def _parse_domain_ip_attribute(self, attribute: MISPAttribute):
        domain, ip = attribute.value.split('|')
        domain_object = self._create_domain_object(domain)
        domain_observable = self._create_observable(domain_object, attribute.uuid, 'DomainName')
        address_object = self._create_address_object(attribute.type, ip)
        address_observable = self._create_observable(address_object, attribute.uuid, 'Address')
        composite_object = ObservableComposition(
            observables=[domain_observable, address_observable]
        )
        composite_object.operator = "AND"
        observable = Observable(
            id_=f"{self.namespace}:ObservableComposition-{attribute.uuid}"
        )
        observable.observable_composition = composite_object
        self._handle_attribute(attribute, observable)

    def _parse_email_attachment(self, attribute: MISPAttribute):
        file_object = File()
        file_object.file_name = attribute.value
        file_object.file_name.condition = "Equals"
        file_object.parent.id_ = f"{self.namespace}:File-{attribute.uuid}"
        email = EmailMessage()
        email.attachments = Attachments()
        email.attachments.append(file_object.parent.id_)
        email.add_related(file_object, "Contains", inline=True)
        email.parent.related_objects[0].id_ = f"{self.namespace}:File-{attribute.uuid}"
        observable = self._create_observable(email, attribute.uuid, 'EmailMessage')
        self._handle_attribute(attribute, observable)

    def _parse_email_attribute(self, attribute: MISPAttribute):
        email_object = EmailMessage()
        email_header = EmailHeader()
        feature = stix1_mapping.email_attribute_mapping[attribute.type]
        setattr(email_header, feature, attribute.value)
        setattr(getattr(email_header, feature), 'condition', 'Equals')
        email_object.header = email_header
        observable = self._create_observable(email_object, attribute.uuid, 'EmailMessage')
        self._handle_attribute(attribute, observable)

    def _parse_file_attribute(self, attribute: MISPAttribute):
        file_object = self._create_file_object(attribute.value)
        observable = self._create_observable(file_object, attribute.uuid, 'File')
        self._handle_attribute(attribute, observable)

    def _parse_hash_attribute(self, attribute: MISPAttribute):
        hash = self._parse_hash_value(attribute.type, attribute.value)
        file_object = File()
        file_object.add_hash(hash)
        observable = self._create_observable(file_object, attribute.uuid, 'File')
        self._handle_attribute(attribute, observable)

    def _parse_hash_composite_attribute(self, attribute: MISPAttribute):
        filename, hash_value = attribute.value.split('|')
        file_object = self._create_file_object(filename)
        attribute_type = attribute.type.split('|')[1] if '|' in attribute.type else 'filename|md5'
        hash = self._parse_hash_value(attribute_type, hash_value)
        file_object.add_hash(hash)
        observable = self._create_observable(file_object, attribute.uuid, 'File')
        self._handle_attribute(attribute, observable)

    @staticmethod
    def _parse_hash_value(attribute_type: str, attribute_value: str):
        args = {'hash_value': attribute_value, 'exact': True}
        if hasattr(Hash, f'TYPE_{attribute_type.upper()}'):
            args['type_'] = getattr(Hash, f'TYPE_{attribute_type.upper()}')
            return Hash(**args)
        hash = Hash(**args)
        _set_hash_type(hash, attribute_value)
        return hash

    def _parse_hostname_attribute(self, attribute: MISPAttribute):
        hostname_object = self._create_hostname_object(attribute.value)
        observable = self._create_observable(hostname_object, attribute.uuid, 'Hostname')
        self._handle_attribute(attribute, observable)

    def _parse_hostname_port_attribute(self, attribute: MISPAttribute):
        hostname, socket_address = self._create_socket_address_object(attribute)
        socket_address.hostname = self._create_hostname_object(hostname)
        observable = self._create_observable(socket_address, attribute.uuid, 'SocketAddress')
        self._handle_attribute(attribute, observable)

    def _parse_http_method_attribute(self, attribute: MISPAttribute):
        http_client_request = HTTPClientRequest()
        http_request_line = HTTPRequestLine()
        http_request_line.http_method = attribute.value
        http_request_line.http_method.condition = "Equals"
        http_client_request.http_request_line = http_request_line
        self._parse_http_session(attribute, http_client_request)

    def _parse_http_session(self, attribute: MISPAttribute, http_client_request: HTTPClientRequest):
        http_request_response = HTTPRequestResponse()
        http_request_response.http_client_request = http_client_request
        http_session_object = HTTPSession()
        http_session_object.http_request_response = http_request_response
        observable = self._create_observable(http_session_object, attribute.uuid, 'HTTPSession')
        self._handle_attribute(attribute, observable)

    def _parse_ip_attribute(self, attribute: MISPAttribute):
        address_object = self._create_address_object(attribute.type, attribute.value)
        observable = self._create_observable(address_object, attribute.uuid, 'Address')
        self._handle_attribute(attribute, observable)

    def _parse_ip_port_attribute(self, attribute: MISPAttribute):
        ip, socket_address = self._create_socket_address_object(attribute)
        socket_address.ip_address = self._create_address_object(attribute.type.split('|')[0], ip)
        observable = self._create_observable(socket_address, attribute.uuid, 'SocketAddress')
        self._handle_attribute(attribute, observable)

    def _parse_mac_address(self, attribute: MISPAttribute):
        network_interface = NetworkInterface()
        network_interface.mac = attribute.value
        network_interface_list = NetworkInterfaceList()
        network_interface_list.append(network_interface)
        system_object = System()
        system_object.network_interface_list = network_interface_list
        observable = self._create_observable(system_object, attribute.uuid, 'System')
        self._handle_attribute(attribute, observable)

    def _parse_malware_sample(self, attribute: MISPAttribute):
        if attribute.data:
            filename, hash_value = attribute.value.split('|')
            artifact_object = self._create_artifact_object(self._get_b64encoded(attribute.data))
            artifact_object.hashes = HashList(self._parse_hash_value('md5', hash_value))
            observable = self._create_observable(artifact_object, attribute.uuid, 'Artifact')
            observable.title = filename
            self._handle_attribute(attribute, observable)
        else:
            self._parse_hash_composite_attribute(attribute)

    def _parse_mutex_attribute(self, attribute: MISPAttribute):
        mutex_object = self._create_mutex_object(attribute.value)
        observable = self._create_observable(mutex_object, attribute.uuid, 'Mutex')
        self._handle_attribute(attribute, observable)

    def _parse_named_pipe(self, attribute: MISPAttribute):
        pipe_object = Pipe()
        pipe_object.named = True
        pipe_object.name = attribute.value
        pipe_object.name.condition = "Equals"
        observable = self._create_observable(pipe_object, attribute.uuid, 'Pipe')
        self._handle_attribute(attribute, observable)

    def _parse_pattern_attribute(self, attribute: MISPAttribute):
        byte_run = ByteRun()
        byte_run.byte_run_data = attribute.value
        file_object = File()
        file_object.byte_runs = ByteRuns(byte_run)
        observable = self._create_observable(file_object, attribute.uuid, 'File')
        self._handle_attribute(attribute, observable)

    def _parse_port_attribute(self, attribute: MISPAttribute):
        port_object = self._create_port_object(attribute.value)
        observable = self._create_observable(port_object, attribute.uuid, 'Port')
        self._handle_attribute(attribute, observable)

    def _parse_regkey_attribute(self, attribute: MISPAttribute):
        registry_key = self._create_registry_key_object(attribute.value)
        observable = self._create_observable(registry_key, attribute.uuid, 'WindowsRegistryKey')
        self._handle_attribute(attribute, observable)

    def _parse_regkey_value_attribute(self, attribute: MISPAttribute):
        regkey, value = attribute.value.split('|')
        registry_key = self._create_registry_key_object(regkey)
        registry_value = RegistryValue()
        registry_value.data = value.strip()
        registry_value.data.condition = "Equals"
        registry_key.values = RegistryValues(registry_value)
        observable = self._create_observable(registry_key, attribute.uuid, 'WindowsRegistryKey')
        self._handle_attribute(attribute, observable)

    def _parse_snort_attribute(self, attribute: MISPAttribute):
        if attribute.to_ids:
            test_mechanism = SnortTestMechanism()
            test_mechanism.rules = [
                {
                    "value": attribute.value,
                    "encoded": True
                }
            ]
            self._parse_test_mechanism(attribute, test_mechanism)
        else:
            self._parse_custom_attribute(self, attribute)

    def _parse_target_attribute(self, attribute: MISPAttribute, identity_spec: STIXCIQIdentity3_0):
        ciq_identity = CIQIdentity3_0Instance()
        ciq_identity.specification = identity_spec
        ciq_identity.id_ = f"{self.namespace}:Identity-{attribute.uuid}"
        ciq_identity.name = f"{attribute.category}: {attribute.value} (MISP Attribute)"
        self.incident.add_victim(ciq_identity)

    def _parse_target_email(self, attribute: MISPAttribute):
        identity_spec = STIXCIQIdentity3_0()
        identity_spec.add_electronic_address_identifier(ElectronicAddressIdentifier(value=attribute.value))
        self._parse_target_attribute(attribute, identity_spec)

    def _parse_target_external(self, attribute: MISPAttribute):
        identity_spec = STIXCIQIdentity3_0()
        identity_spec.party_name = PartyName(name_lines=[f"External target: {attribute.value}"])
        self._parse_target_attribute(attribute, identity_spec)

    def _parse_target_location(self, attribute: MISPAttribute):
        identity_spec = STIXCIQIdentity3_0()
        identity_spec.add_address(ciq_Address(FreeTextAddress(address_lines=[attribute.value])))
        self._parse_target_attribute(attribute, identity_spec)

    def _parse_target_machine(self, attribute: MISPAttribute):
        affected_asset = AffectedAsset()
        description = attribute.value
        if hasattr(attribute, 'comment') and attribute.comment:
            description = f"{description} ({attribute.comment})"
        affected_asset.description = description
        self.incident.affected_assets.append(affected_asset)

    def _parse_target_org(self, attribute: MISPAttribute):
        identity_spec = STIXCIQIdentity3_0()
        identity_spec.party_name = PartyName(organisation_names=[attribute.value])
        self._parse_target_attribute(attribute, identity_spec)

    def _parse_target_user(self, attribute: MISPAttribute):
        identity_spec = STIXCIQIdentity3_0()
        identity_spec.party_name = PartyName(person_names=[attribute.value])
        self._parse_target_attribute(attribute, identity_spec)

    def _parse_test_mechanism(self, attribute: MISPAttribute, test_mechanism: Union[SnortTestMechanism, YaraTestMechanism]):
        indicator = self._create_indicator_from_attribute(attribute)
        tags = self._handle_attribute_tags_and_galaxies(attribute, indicator)
        if tags:
            indicator.handling = self._set_handling(tags)
        indicator.add_indicator_type("Malware Artifacts")
        indicator.add_valid_time_position(ValidTime())
        indicator.add_test_mechanism(test_mechanism)
        related_indicator = RelatedIndicator(indicator, relationship=attribute.category)
        self.incident.related_indicators.append(related_indicator)

    def _parse_url_attribute(self, attribute: MISPAttribute):
        uri_object = self._create_uri_object(attribute.value)
        observable = self._create_observable(uri_object, attribute.uuid, 'URI')
        self._handle_attribute(attribute, observable)

    def _parse_undefined_attribute(self, attribute: MISPAttribute):
        if hasattr(attribute, 'comment') and attribute.comment == 'Imported from STIX header description':
            self.header_comment.append(attribute.value)
        else:
            self._add_journal_entry(f"Attribute ({attribute.category} - {attribute.type}): {attribute.value}")

    def _parse_user_agent_attribute(self, attribute: MISPAttribute):
        http_client_request = HTTPClientRequest()
        http_request_header = HTTPRequestHeader()
        header_fields = HTTPRequestHeaderFields()
        header_fields.user_agent = attribute.value
        header_fields.user_agent.condition = "Equals"
        http_request_header.parsed_header = header_fields
        http_client_request.http_request_header = http_request_header
        self._parse_http_session(attribute, http_client_request)

    def _parse_vulnerability_attribute(self, attribute: MISPAttribute):
        ttp = self._create_ttp(attribute)
        vulnerability = Vulnerability()
        vulnerability.cve_id = attribute.value
        exploit_target = ExploitTarget(timestamp=attribute.timestamp)
        exploit_target.id_ = f"{self.namespace}:ExploitTarget-{attribute.uuid}"
        if hasattr(attribute, 'comment') and attribute.comment != "Imported via the freetext import.":
            exploit_target.title = attribute.comment
        else:
            exploit_target.title = f"Vulnerability {attribute.value}"
        exploit_target.add_vulnerability(vulnerability)
        ttp.add_exploit_target(exploit_target)
        related_ttp = self._create_related_ttp(ttp.id_, attribute.type)
        self._handle_related_ttps({attribute.uuid: related_ttp})
        self.ttps[attribute.uuid] = ttp

    def _parse_windows_service_attribute(self, attribute: MISPAttribute):
        windows_service = WinService()
        feature = 'service_name' if attribute.type == 'windows-service-name' else 'display_name'
        setattr(windows_service, feature, attribute.value)
        observable = self._create_observable(windows_service, attribute.uuid, 'WindowsService')
        self._handle_attribute(attribute, observable)

    def _parse_x509_fingerprint_attribute(self, attribute: MISPAttribute):
        x509_signature = X509CertificateSignature()
        signature_algorithm = attribute.type.split('-')[-1].upper()
        for feature, value in zip(('signature', 'signature_algorithm'), (attribute.value, signature_algorithm)):
            setattr(x509_signature, feature, value)
            setattr(getattr(x509_signature, feature), 'condition', 'Equals')
        x509_certificate = X509Certificate()
        x509_certificate.certificate_signature = x509_signature
        observable = self._create_observable(x509_certificate, attribute.uuid, 'X509Certificate')
        self._handle_attribute(attribute, observable)

    def _parse_yara_attribute(self, attribute: MISPAttribute):
        if attribute.to_ids:
            test_mechanism = YaraTestMechanism()
            test_mechanism.rule = {
                "value": attribute.value,
                "encoded": True
            }
            self._parse_test_mechanism(attribute, test_mechanism)
        else:
            self._parse_custom_attribute(self, attribute)

    ################################################################################
    #                        MISP OBJECTS PARSING FUNCTIONS                        #
    ################################################################################

    def _resolve_objects(self):
        for misp_object in self.misp_event.objects:
            object_name = misp_object.name
            if object_name == 'original-imported-file':
                continue
            # try:
            to_ids = self._fetch_ids_flags(misp_object.attributes)
            to_call = self._fetch_objects_mapping_function(object_name)
            observable = getattr(self, to_call)(misp_object)
            if to_ids:
                self._handle_misp_object_with_context(misp_object, observable)
            else:
                self._handle_misp_object(observable, misp_object.get('meta-category'))
            # except Exception:
            #     self.errors.append(f'Error with the {object_name} object: {misp_object.uuid}.')

    @staticmethod
    def _extract_multiple_object_attributes(attributes):
        attributes_dict = defaultdict(list)
        for attribute in attributes:
            attributes_dict[attribute.object_relation].append(attribute.value)
        return attributes_dict

    @staticmethod
    def _extract_object_attributes(attributes):
        return {attribute.object_relation: attribute.value for attribute in attributes}

    @staticmethod
    def _fetch_ids_flags(attributes: list) -> bool:
        for attribute in attributes:
            if attribute.to_ids:
                return True
        return False

    @staticmethod
    def _fetch_objects_mapping_function(object_name: str) -> str:
        if object_name in stix1_mapping.objects_mapping:
            return stix1_mapping.objects_mapping[object_name]
        return '_parse_custom_object'

    def _handle_misp_object(self, observable: Observable, category: str):
        related_observable = RelatedObservable(
            observable,
            relationship=category
        )
        self.incident.related_observables.append(related_observable)

    def _handle_misp_object_with_context(self, misp_object: MISPObject, observable: Observable):
        indicator = self._create_indicator_from_object(misp_object)
        indicator.add_indicator_type(self._set_indicator_type(misp_object.name))
        indicator.add_valid_time_position(ValidTime())
        indicator.add_observable(observable)
        tags = self._handle_object_tags_and_galaxies(misp_object, indicator)
        if tags:
            indicator.handling = self._set_handling(tags)
        related_indicator = RelatedIndicator(
            indicator,
            relationship=misp_object.get('meta-category')
        )
        self.incident.related_indicators.append(related_indicator)

    def _handle_object_tags_and_galaxies(self, misp_object: MISPObject, indicator: Indicator) -> tuple:
        tags = defaultdict(set)
        galaxies = defaultdict(list)
        for attribute in misp_object.attributes:
            if attribute.get('Galaxy'):
                galaxy_type = galaxy['type']
                if galaxy_type in stix1_mapping.galaxy_types_mapping:
                    galaxies[galaxy_type].append(galaxy)
            if attribute.tags:
                tags['tags'].update(tag.name for tag in attribute.tags)
        if galaxies:
            for galaxy_type, galaxy in galaxies.items():
                if galaxy_type in stix1_mapping.galaxy_types_mapping:
                    to_call = stix1_mapping.galaxy_types_mapping[galaxy_type]
                    getattr(self, to_call.format('attribute'))(galaxy, indicator)
                    tags['tag_names'].update(self._quick_fetch_tag_names(galaxy))
                else:
                    self.warnings.add(f'{galaxy_type} galaxy in {misp_object.name} object not mapped.')
            return tuple(tag for tag in tags['tags'] if tag not in tags['tag_names'])
        return tuple(tag for tag in tags['tags'])

    def _parse_asn_object(self, misp_object: MISPObject) -> Observable:
        attributes = self._extract_multiple_object_attributes(misp_object.attributes)
        as_object = self._create_autonomous_system_object(attributes.pop('asn')[0])
        if 'description' in attributes:
            as_object.name = attributes.pop('description')[0]
        if attributes:
            as_object.custom_properties = CustomProperties()
            for object_relation, values in attributes.items():
                for value in values:
                    prop = Property()
                    prop.name = object_relation
                    prop.value = value
                    as_object.custom_properties.append(prop)
        observable = self._create_observable(as_object, misp_object.uuid, 'AS')
        return observable

    def _parse_credential_object(self, misp_object: MISPObject) -> Observable:
        attributes = self._extract_multiple_object_attributes(misp_object.attributes)
        account_object = self._create_account_object()
        if 'username' in attributes:
            account_object.username = attributes.pop('username')

    def _parse_custom_object(self, misp_object: MISPObject) -> Observable:
        custom_object = Custom()
        custom_object.custom_name = misp_object.name
        if misp_object.get('description'):
            custom_object.description = misp_object.description
        custom_object.custom_properties = CustomProperties()
        for attribute in misp_object.attributes:
            prop = Property()
            prop.name = attribute.object_relation
            prop.value = attribute.value
            custom_object.custom_properties.append(prop)
        observable = self._create_observable(custom_object, misp_object.uuid, 'Custom')
        self.warnings.add(f'MISP Object name {misp_object.name} not mapped.')
        return observable

    ################################################################################
    #                          GALAXIES PARSING FUNCTIONS                          #
    ################################################################################

    def _get_related_ttps(self, galaxy: dict, feature: str) -> dict:
        related_ttps = {}
        for cluster in galaxy['GalaxyCluster']:
            cluster_uuid = cluster['uuid']
            if cluster_uuid in self.ttps:
                related_ttps[cluster_uuid] = self._create_related_ttp(
                    self.ttps[cluster_uuid].id_,
                    galaxy['name']
                )
                continue
            ttp = self._create_ttp_from_galaxy(galaxy['name'], cluster_uuid)
            getattr(self, f'_parse_{feature}_galaxy')(cluster, ttp)
            related_ttps[cluster_uuid] = self._create_related_ttp(ttp.id_, galaxy['name'])
            self.ttps[cluster_uuid] = ttp
        return related_ttps

    def _handle_related_ttps(self, related_ttps: dict):
        for uuid, related_ttp in related_ttps.items():
            if uuid not in self.contextualised_data['ttp']:
                self.contextualised_data['ttp'][uuid] = related_ttp

    def _parse_attack_pattern_attribute_galaxy(self, galaxy: dict, indicator: Indicator):
        related_ttps = self._get_related_ttps(galaxy, 'attack_pattern')
        for related_ttp in related_ttps.values():
            indicator.add_indicated_ttp(related_ttp)

    def _parse_attack_pattern_event_galaxy(self, galaxy:dict):
        related_ttps = self._get_related_ttps(galaxy, 'attack_pattern')
        self._handle_related_ttps(related_ttps)

    def _parse_attack_pattern_galaxy(self, cluster: dict, ttp: TTP):
        behavior = Behavior()
        attack_pattern = AttackPattern()
        attack_pattern.id_ = f"{self.namespace}:AttackPattern-{cluster['uuid']}"
        attack_pattern.title = cluster['value']
        attack_pattern.description = cluster['description']
        if cluster['meta'].get('external_id'):
            external_id = cluster['meta']['external_id'][0]
            if external_id.startswith('CAPEC'):
                attack_pattern.capec_id = external_id
        behavior.add_attack_pattern(attack_pattern)
        ttp.behavior = behavior

    def _parse_course_of_action_attribute_galaxy(self, galaxy: dict, indicator: Indicator):
        for cluster in galaxy['GalaxyCluster']:
            cluster_uuid = cluster['uuid']
            if cluster_uuid in self.courses_of_action:
                related_coa = self._create_related_coa(
                    self.courses_of_action[cluster_uuid].id_,
                    galaxy['name']
                )
                indicator.suggested_coas.append(related_coa)
                continue
            course_of_action = self._create_course_of_action_from_galaxy(cluster)
            related_coa = self._create_related_coa(course_of_action.id_, galaxy['name'])
            indicator.suggested_coas.append(related_coa)
            self.courses_of_action[cluster_uuid] = course_of_action

    def _parse_course_of_action_event_galaxy(self, galaxy: dict):
        for cluster in galaxy['GalaxyCluster']:
            cluster_uuid = cluster['uuid']
            if cluster_uuid in self.courses_of_action:
                coa_taken = self._create_coa_taken(self.courses_of_action[cluster_uuid].id_)
                self.contextualised_data['course_of_action'][cluster_uuid] = coa_taken
                continue
            course_of_action = self._create_course_of_action_from_galaxy(cluster)
            coa_taken = self._create_coa_taken(course_of_action.id_)
            self.contextualised_data['course_of_action'][cluster_uuid] = coa_taken
            self.courses_of_action[cluster_uuid] = course_of_action

    def _parse_malware_attribute_galaxy(self, galaxy: dict, indicator: Indicator):
        related_ttps = self._get_related_ttps(galaxy, 'malware')
        for related_ttp in related_ttps.values():
            indicator.add_indicated_ttp(related_ttp)

    def _parse_malware_event_galaxy(self, galaxy: dict):
        related_ttps = self._get_related_ttps(galaxy, 'malware')
        self._handle_related_ttps(related_ttps)

    def _parse_malware_galaxy(self, cluster: dict, ttp: TTP):
        behavior = Behavior()
        malware = MalwareInstance()
        malware.id_ = f"{self.namespace}:MalwareInstance-{cluster['uuid']}"
        malware.title = cluster['value']
        if cluster.get('description'):
            malware.description = cluster['description']
        if cluster['meta'].get('synonyms'):
            for synonym in cluster['meta']['synonyms']:
                malware.add_name(synonym)
        behavior.add_malware_instance(malware)
        ttp.behavior = behavior

    def _parse_threat_actor_galaxy(self, galaxy: dict):
        for cluster in galaxy['GalaxyCluster']:
            cluster_uuid = cluster['uuid']
            if cluster_uuid not in self.contextualised_data['threat_actor']:
                if cluster_uuid in self.threat_actors:
                    related_threat_actor = self._create_related_threat_actor(
                        self.threat_actors[cluster_uuid].id_,
                        galaxy['name']
                    )
                    self.contextualised_data['threat_actor'][cluster_uuid] = related_threat_actor
                    continue
                threat_actor = self._create_threat_actor_from_galaxy(cluster)
                related_threat_actor = self._create_related_threat_actor(
                    threat_actor.id_,
                    galaxy['name']
                )
                self.contextualised_data['threat_actor'][cluster_uuid] = related_threat_actor
                self.threat_actors[cluster_uuid] = threat_actor

    def _parse_tool_attribute_galaxy(self, galaxy: dict, indicator: Indicator):
        related_ttps = self._get_related_ttps(galaxy, 'tool')
        for related_ttp in related_ttps.values():
            indicator.add_indicated_ttp(related_ttp)

    def _parse_tool_event_galaxy(self, galaxy: dict):
        related_ttps = self._get_related_ttps(galaxy, 'tool')
        self._handle_related_ttps(related_ttps)

    def _parse_tool_galaxy(self, cluster: dict, ttp: TTP):
        tools = Tools()
        tool = ToolInformation()
        tool.id_ = f"{self.namespace}:ToolInformation-{cluster['uuid']}"
        tool.name = cluster['value']
        if cluster.get('description'):
            tool.description = cluster['description']
        tools.append(tool)
        resource = Resource()
        resource.tools = tools
        ttp.resources = resource

    def _parse_vulnerability_attribute_galaxy(self, galaxy: dict, indicator: Indicator):
        related_ttps = self._get_related_ttps(galaxy, 'vulnerability')
        for related_ttp in related_ttps.values():
            indicator.add_indicated_ttp(related_ttp)

    def _parse_vulnerability_event_galaxy(self, galaxy: dict):
        related_ttps = self._get_related_ttps(galaxy, 'vulnerability')
        self._handle_related_ttps(related_ttps)

    def _parse_vulnerability_galaxy(self, cluster: dict, ttp: TTP):
        exploit_target = ExploitTarget()
        exploit_target.id_ = f"{self.namespace}:ExploitTarget-{cluster['uuid']}"
        vulnerability = Vulnerability()
        vulnerability.id_ = f"{self.namespace}:Vulnerability-{cluster['uuid']}"
        vulnerability.title = cluster['value']
        vulnerability.description = cluster['description']
        if cluster['meta'].get('aliases'):
            vulnerability.cve_id = cluster['meta']['aliases'][0]
        if cluster['meta'].get('refs'):
            for reference in cluster['meta']['refs']:
                vulnerability.add_reference(reference)
        exploit_target.add_vulnerability(vulnerability)
        ttp.add_exploit_target(exploit_target)

    ################################################################################
    #                      OBJECTS CREATION HELPER FUNCTIONS.                      #
    ################################################################################

    def _add_journal_entry(self, entryline: str):
        history_item = HistoryItem()
        history_item.journal_entry = entryline
        try:
            self.incident.history.append(history_item)
        except AttributeError:
            self.incident.history = History()
            self.incident.history.append(history_item)

    @staticmethod
    def _create_address_object(attribute_type: str, attribute_value: str) -> Address:
        address_object = Address()
        if '/' in attribute_value:
            address_object.category = "cidr"
            condition = "Contains"
        else:
            try:
                socket.inet_aton(attribute_value)
                address_object.category = "ipv4-addr"
            except socket.error:
                address_object.category = "ipv6-addr"
            condition = "Equals"
        if 'src' in attribute_type:
            address_object.is_source = True
            address_object.is_destination = False
        else:
            address_object.is_source = False
            address_object.is_destination = True
        address_object.address_value = attribute_value
        address_object.address_value.condition = condition
        return address_object

    @staticmethod
    def _create_artifact_object(data: str) -> Artifact:
        raw_artifact = RawArtifact(data)
        artifact = Artifact()
        artifact.raw_artifact = raw_artifact
        artifact.raw_artifact.condition = "Equals"
        return artifact

    @staticmethod
    def _create_autonomous_system_object(AS: str) -> AutonomousSystem:
        autonomous_system = AutonomousSystem()
        feature = 'handle' if AS.startswith('AS') else 'number'
        setattr(autonomous_system, feature, AS)
        setattr(getattr(autonomous_system, feature), 'condition', 'Equals')
        return autonomous_system

    @staticmethod
    def _create_coa_taken(coa_id: str, timestamp: Optional[datetime] = None) -> COATaken:
        coa = CourseOfAction(idref=coa_id)
        if timestamp is not None:
            coa.timestamp = timestamp
        coa_taken = COATaken(coa)
        return coa_taken

    def _create_course_of_action_from_galaxy(self, cluster: dict) -> CourseOfAction:
        course_of_action = CourseOfAction()
        course_of_action.id_ = f"{self.namespace}:CourseOfAction-{cluster['uuid']}"
        course_of_action.title = cluster['value']
        course_of_action.description = cluster['description']
        return course_of_action

    @staticmethod
    def _create_domain_object(domain: str) -> DomainName:
        domain_object = DomainName()
        domain_object.value = domain
        domain_object.value.condition = "Equals"
        return domain_object

    @staticmethod
    def _create_file_object(filename: str) -> File:
        file_object = File()
        file_object.file_name = filename
        file_object.file_name.condition = "Equals"
        return file_object

    @staticmethod
    def _create_hostname_object(hostname: str) -> Hostname:
        hostname_object = Hostname()
        hostname_object.hostname_value = hostname
        hostname_object.hostname_value.condition = "Equals"
        return hostname_object

    def _create_indicator_from_attribute(self, attribute: MISPAttribute) -> Indicator:
        indicator = Indicator(timestamp=attribute.timestamp)
        indicator.id_ = f"{self.namespace}:Indicator-{attribute.uuid}"
        indicator.producer = self._set_producer()
        indicator.title = f"{attribute.category}: {attribute.value} (MISP Attribute)"
        indicator.description = attribute.comment if attribute.get('comment') else indicator.title
        indicator.confidence = Confidence(
            value=stix1_mapping.confidence_value,
            description=stix1_mapping.confidence_description,
            timestamp=attribute.timestamp
        )
        return indicator

    def _create_indicator_from_object(self, misp_object: MISPObject) -> Indicator:
        indicator = Indicator(timestamp=misp_object.timestamp)
        indicator.id_ = f"{self.namespace}:Indicator-{misp_object.uuid}"
        indicator.producer = self._set_producer()
        indicator.title = f"{misp_object.get('meta-category')}: {misp_object.name} (MISP Object)"
        indicator.description = misp_object.comment if misp_object.get('comment') else misp_object.description
        indicator.confidence = Confidence(
            value=stix1_mapping.confidence_value,
            description=stix1_mapping.confidence_description,
            timestamp=misp_object.timestamp
        )
        return indicator

    @staticmethod
    def _create_information_source(identity: Identity) -> InformationSource:
        information_source = InformationSource(identity=identity)
        return information_source

    @staticmethod
    def _create_mutex_object(name: str) -> Mutex:
        mutex_object = Mutex()
        mutex_object.name = name
        mutex_object.name.condition = "Equals"
        return mutex_object

    def _create_observable(self, stix_object: _OBSERVABLE_OBJECT_TYPES, attribute_uuid: str, feature: str) -> Observable:
        stix_object.parent.id_ = f"{self.namespace}:{feature}-{attribute_uuid}"
        observable = Observable(stix_object)
        observable.id_ = f"{self.namespace}:Observable-{attribute_uuid}"
        return observable

    @staticmethod
    def _create_port_object(port: str) -> Port:
        port_object = Port()
        port_object.port_value = port
        port_object.port_value.condition = "Equals"
        return port_object

    @staticmethod
    def _create_registry_key_object(regkey: str) -> WinRegistryKey:
        registry_key = WinRegistryKey()
        registry_key.key = regkey.strip()
        registry_key.key.condition = "Equals"
        return registry_key

    @staticmethod
    def _create_related_coa(coa_id: str, category: str, timestamp: Optional[datetime] = None) -> RelatedCOA:
        coa = CourseOfAction(idref=coa_id)
        if timestamp is not None:
            coa.timestamp = timestamp
        related_coa = RelatedCOA(coa, relationship=category)
        return related_coa

    @staticmethod
    def _create_related_threat_actor(ta_id: str, category: str, timestamp: Optional[datetime] = None) -> RelatedThreatActor:
        rta = ThreatActor(idref=ta_id)
        if timestamp is not None:
            rta.timestamp = timestamp
        related_ta = RelatedThreatActor(rta, relationship=category)
        return related_ta

    @staticmethod
    def _create_related_ttp(ttp_id: str, category: str, timestamp: Optional[datetime] = None) -> RelatedTTP:
        rttp = TTP(idref=ttp_id)
        if timestamp is not None:
            rttp.timestamp = timestamp
        related_ttp = RelatedTTP(rttp, relationship=category)
        return related_ttp

    def _create_socket_address_object(self, attribute: MISPAttribute) -> list([str, SocketAddress]):
        value, port = attribute.value.split('|')
        socket_address = SocketAddress()
        socket_address.port = self._create_port_object(port)
        return value, socket_address

    def _create_threat_actor_from_galaxy(self, cluster: dict) -> ThreatActor:
        threat_actor = ThreatActor()
        threat_actor.id_ = f"{self.namespace}:ThreatActor-{cluster['uuid']}"
        threat_actor.title = cluster['value']
        if cluster.get('description'):
            threat_actor.description = cluster['description']
        meta = cluster['meta']
        if meta.get('cfr-type-of-incident'):
            intended_effect = meta['cfr-type-of-incident']
            if isinstance(intended_effect, list):
                for effect in intended_effect:
                    threat_actor.add_intended_effect(effect)
            else:
                threat_actor.add_intended_effect(intended_effect)
        return threat_actor

    def _create_ttp(self, attribute: MISPAttribute) -> TTP:
        ttp = TTP(timestamp=attribute.timestamp)
        ttp.id_ = f"{self.namespace}:TTP-{attribute.uuid}"
        if attribute.tags:
            tags = tuple(tag.name for tag in attribute.tags)
            ttp.handling = self._set_handling(tags)
        ttp.title = f"{attribute.category}: {attribute.value} (MISP Attribute)"
        return ttp

    def _create_ttp_from_galaxy(self, galaxy_name: str, uuid: str) -> TTP:
        ttp = TTP()
        ttp.id_ = f'{self.namespace}:TTP-{uuid}'
        ttp.title = f'{galaxy_name} (MISP Galaxy)'
        return ttp

    @staticmethod
    def _create_uri_object(url: str) -> URI:
        uri_object = URI(value=url, type_='URL')
        uri_object.value.condition = "Equals"
        return uri_object

    @staticmethod
    def _fetch_colors(tags: list) -> tuple:
        return (tag.split(':')[-1].upper() for tag in tags)

    @staticmethod
    def _set_color(colors: list) -> str:
        tlp_color = 0
        for color in colors:
            color_num = stix1_mapping.TLP_order[color]
            if color_num > tlp_color:
                tlp_color = color_num
                color_value = color
        return color_value

    def _set_creator(self) -> str:
        if hasattr(self.misp_event, 'orgc'):
            return self.misp_event.orgc.name
        return self.orgname

    def _set_handling(self, tags: list) -> Marking:
        sorted_tags = defaultdict(list)
        for tag in tags:
            feature = 'tlp_tags' if tag.startswith('tlp:') else 'simple_tags'
            sorted_tags[feature].append(tag)
        handling = Marking()
        marking_specification = MarkingSpecification()
        if 'tlp_tags' in sorted_tags:
            tlp_marking = TLPMarkingStructure()
            tlp_marking.color = self._set_color(self._fetch_colors(sorted_tags['tlp_tags']))
            marking_specification.marking_structures.append(tlp_marking)
        for tag in sorted_tags['simple_tags']:
            simple_marking = SimpleMarkingStructure()
            simple_marking.statement = tag
            marking_specification.marking_structures.append(simple_marking)
        handling.add_marking(marking_specification)
        return handling

    @staticmethod
    def _set_indicator_type(attribute_type: str) -> str:
        if attribute_type in stix1_mapping.misp_indicator_type:
            return stix1_mapping.misp_indicator_type[attribute_type]
        return 'Malware Artifacts'

    def _set_producer(self) -> Identity:
        identity = Identity(name=self.orgc_name)
        information_source = InformationSource(identity=identity)
        return information_source

    def _set_reporter(self) -> Identity:
        reporter = self.misp_event.org.name if hasattr(self.misp_event, 'org') else self.orgname
        identity = Identity(name=reporter)
        return self._create_information_source(identity)

    def _set_source(self) -> Identity:
        identity = Identity(name=self.orgc_name)
        return self._create_information_source(identity)

    ################################################################################
    #                              UTILITY FUNCTIONS.                              #
    ################################################################################

    @staticmethod
    def _get_b64encoded(data):
        return b64encode(data.getvalue()).decode()

    @staticmethod
    def _from_datetime_to_str(date):
        return date.strftime("%Y-%m-%dT%H:%M:%S+00:00")

    @staticmethod
    def _quick_fetch_tag_names(galaxy: dict) -> list:
        attribute_galaxies = []
        for cluster in galaxy['GalaxyCluster']:
            attribute_galaxies.append(f'misp-galaxy:{galaxy["type"]}="{cluster["value"]}"')
        return attribute_galaxies
