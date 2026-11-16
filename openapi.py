import argparse
import contextlib
import copy
import dataclasses
import getpass
import pathlib
import ssl
import sys
import typing
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree
from xml.sax.saxutils import escape

SOAP_NAMESPACE = "http://schemas.xmlsoap.org/soap/envelope/"
OPENAPI_VERSION_MAJOR = 7
OPENAPI_VERSION_MINOR = 34
OPENAPI_NAMESPACE = f"http://www.konicaminolta.com/service/OpenAPI-{OPENAPI_VERSION_MAJOR}-{OPENAPI_VERSION_MINOR}"


class OpenAPIError(RuntimeError):
    def __init__(
        self, msg: str, data: bytes | xml.etree.ElementTree.Element | None = None
    ) -> None:
        super().__init__(msg)
        self.data: typing.Final = data

    def __str__(self) -> str:
        message = super().__str__()
        if isinstance(self.data, bytes):
            additional = self.data.decode("utf-8", errors="replace")
        elif isinstance(self.data, xml.etree.ElementTree.Element):
            clone = copy.deepcopy(self.data)
            xml.etree.ElementTree.indent(clone)
            additional = xml.etree.ElementTree.tostring(clone, encoding="unicode")
        else:
            additional = str(self.data or "")
        return f"{message}\n{additional}" if additional else message


@dataclasses.dataclass(frozen=True, order=True, kw_only=True)
class OpenAPISetting:
    name: str
    action: str
    request_item: str | None
    element: str | typing.Iterable[str]
    readonly: bool = False
    payload: str = ""


class OpenAPIArguments(typing.TypedDict):
    url: str
    config: typing.NotRequired[pathlib.Path]
    insecure: typing.NotRequired[bool]
    filter: typing.NotRequired[typing.Iterable[str]]
    password: str
    additional_settings: typing.NotRequired[typing.Iterable[OpenAPISetting]]


def parse_args() -> OpenAPIArguments:
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="OpenAPI URL")
    parser.add_argument(
        "--config",
        type=pathlib.Path,
        help="configuration XML file path (default: stdio)",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="turn off SSL verification (default: on)",
    )
    parser.add_argument(
        "--filter",
        nargs="+",
        help="settings to handle (default: all)",
    )
    parser.add_argument("--password", help="admin password (default: prompt)")
    args = parser.parse_args()
    result: OpenAPIArguments = {
        "url": args.url,
        "insecure": args.insecure,
        "password": (
            getpass.getpass("Password: ") if args.password is None else args.password
        ),
    }
    if args.filter:
        result["filter"] = frozenset(args.filter)
    if args.config is not None:
        result["config"] = args.config
    return result


class OpenAPI:
    def __init__(self, **args: typing.Unpack[OpenAPIArguments]):
        try:
            parts = urllib.parse.urlsplit(args["url"])
            hostname = parts.hostname
            port = parts.port
        except ValueError as error:
            raise ValueError(f"OpenAPI URL is invalid: {error}") from error
        if parts.scheme not in {"http", "https"}:
            raise ValueError(f"unsupported OpenAPI URL scheme: {parts.scheme}")
        if hostname is None:
            raise ValueError("OpenAPI URL is missing a hostname")
        netloc = f"[{hostname}]" if ":" in hostname else hostname
        if port is not None:
            netloc = f"{netloc}:{port}"
        if args.get("insecure", False):
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
        else:
            ssl_context = None
        self.settings: typing.Final = dict[str, OpenAPISetting]()
        for settings in (
            self.browser_settings,
            self.configuration_settings,
            self.device_info_detail_settings,
            self.device_permanent_settings,
            self.other_settings,
            args.get("additional_settings", ()),
        ):
            for setting in settings:
                name_ignore_case = setting.name.casefold()
                if name_ignore_case in self.settings:
                    raise ValueError(f"setting '{setting.name}' already registered")
                self.settings[name_ignore_case] = setting
        self.url: typing.Final = urllib.parse.urlunsplit(
            (parts.scheme, netloc, parts.path, parts.query, "")
        )
        self.username: typing.Final = urllib.parse.unquote(parts.username or "")
        self.password: typing.Final = urllib.parse.unquote(parts.password or "")
        self.admin_password: typing.Final = args["password"]
        self.config: typing.Final = args.get("config")
        filter_names = list(args.get("filter", ()))
        for name in filter_names:
            if name.casefold() not in self.settings:
                raise ValueError(f"filter '{name}' does not match any setting")
        self.filter: typing.Final = frozenset(name.casefold() for name in filter_names)
        self.ssl_context: typing.Final = ssl_context
        self.auth_key: str | None = None
        self.lock_key: str | None = None

    @property
    def browser_settings(self) -> typing.Iterable[OpenAPISetting]:
        yield OpenAPISetting(
            name="WebBrowser",
            action="WebBrowser",
            request_item=None,
            element="EnableWebBrowser",
        )
        yield OpenAPISetting(
            name="WebBrowserBookmark",
            action="BrowserSetting",
            request_item="Bookmark",
            element="Bookmarklist",
        )
        for setting in ("Content", "Operation"):
            yield OpenAPISetting(
                name=f"WebBrowser{setting}",
                action="BrowserSetting",
                request_item=f"{setting}Setting",
                element=f"{setting}Setting",
            )

    @property
    def configuration_settings(self) -> typing.Iterable[OpenAPISetting]:
        for kind in ("Public", "User"):
            yield OpenAPISetting(
                name=f"{kind}AbbrConfiguration",
                action="AbbrConfiguration",
                request_item=None,
                element=("Abbr", "GroupAbbr"),
                payload=f"<AddressKind>{kind}</AddressKind>",
                readonly=True,
            )
        yield OpenAPISetting(
            name="BoxConfiguration",
            action="BoxConfiguration",
            request_item=None,
            element="BoxConfiguration",
            readonly=True,
        )
        for setting in ("CopyProgram", "ProgramKey"):
            yield OpenAPISetting(
                name=f"{setting}Configuration",
                action=f"{setting}Configuration",
                request_item=None,
                element=f"{setting}Info",
                readonly=True,
            )

    @property
    def device_info_detail_settings(self) -> typing.Iterable[OpenAPISetting]:
        for simple_setting in (
            "AirPrint",
            "AppleTalk",
            "Bonjour",
            "ExpansionNetwork",
            "Fax",
            "Filtering",
            "FtpClient",
            "Ftpd",
            "Http",
            "Ieee8021x",
            "Iisw",
            "Input",
            "Ipp",
            "Ldap",
            "Lpr",
            "MailReceive",
            "MailReceiveSmtp",
            "MailSend",
            "Netware",
            "NetworkFax",
            "Ntp",
            "OpenApi",
            "RawPort",
            "RemoteFwUpdate",
            "Slp",
            "Smime",
            "Snmp",
            "System",
            "SystemConnection",
            "TcpipGeneral",
            "TcpSocket",
            "VLAN",
            "WindowsNetwork",
        ):
            yield OpenAPISetting(
                name=simple_setting,
                action="DeviceInfoDetail",
                request_item=simple_setting,
                element=simple_setting,
            )
        for readonly_setting in (
            "FwRollback",
            "Marker",
            "MediaPath",
            "Odh",
            "Output",
            "PdlInfoList",
            "ReportPrintList",
            "Sensor",
            "Storage",
        ):
            yield OpenAPISetting(
                name=readonly_setting,
                action="DeviceInfoDetail",
                request_item=readonly_setting,
                element=readonly_setting,
                readonly=True,
            )
        for setting_with_element_suffix in (
            "Bmlinks",
            "CertificateVerification",
            "Ssdp",
            "WebDav",
            "WebService",
        ):
            yield OpenAPISetting(
                name=setting_with_element_suffix,
                action="DeviceInfoDetail",
                request_item=setting_with_element_suffix,
                element=f"{setting_with_element_suffix}Setting",
            )
        for readonly_list_setting in ("Consumable", "Version"):
            yield OpenAPISetting(
                name=readonly_list_setting,
                action="DeviceInfoDetail",
                request_item=readonly_list_setting,
                element=f"{readonly_list_setting}List",
                readonly=True,
            )
        yield OpenAPISetting(
            name="MarketArea",
            action="DeviceInfoDetail",
            request_item="MarketArea",
            element=(
                "GreekEnable",
                "HebrewEnable",
                "MarketArea",
                "MarketArea2",
                "FaxMarketArea",
                "Fls",
                "Type8K",
                "Type16K",
            ),
            readonly=True,
        )
        yield OpenAPISetting(
            name="PhysicalInterface",
            action="DeviceInfoDetail",
            request_item="PhysicalInterface",
            element="Physical",
        )

    @property
    def device_permanent_settings(self) -> typing.Iterable[OpenAPISetting]:
        for simple_setting in (
            "AcquirePermission",
            "Admin",
            "AdminMyPanel",
            "Alert",
            "AuthDevice",
            "Box",
            "CloudConnection",
            "CommonPaperHandling",
            "Copy",
            "Counter",
            "CounterReport",
            "CustomPanel",
            "CustomPaper",
            "DateTime",
            "ExpertAdjust",
            "HeaderFooter",
            "HeaderFooterForPanel",
            "Ifax",
            "InternalWebServer",
            "LightPanelTopMenu",
            "MfpService",
            "Network",
            "OperationLevel",
            "Panel",
            "PanelTopMenu",
            "Power",
            "Print",
            "Pswc",
            "ReferLicence",
            "Scan",
            "SearchOption",
            "Security",
            "SingleSignOnDomain",
            "SingleSignOnService",
            "SIPSetting",
            "Size",
            "UbiquitousPrint",
            "WeeklyTimer",
        ):
            yield OpenAPISetting(
                name=simple_setting,
                action="DevicePermanentSetting",
                request_item=simple_setting,
                element=simple_setting,
            )
        for duplicate_setting in ("Fax", "NetworkFax", "System"):
            # we use Setting suffix to not clash with DeviceInfoDetail
            yield OpenAPISetting(
                name=f"{duplicate_setting}Setting",
                action="DevicePermanentSetting",
                request_item=duplicate_setting,
                element=duplicate_setting,
            )
        yield OpenAPISetting(
            name="Authentication",
            action="DevicePermanentSetting",
            request_item="Authentication",
            element="AuthSetting",
        )
        yield OpenAPISetting(
            name="Tsi",
            action="DevicePermanentSetting",
            request_item="Tsi",
            element="TsiConfiguration",
        )

    @property
    def other_settings(self) -> typing.Iterable[OpenAPISetting]:
        yield OpenAPISetting(
            name="CustomApplicationSetting",
            action="CustomApplicationSetting",
            request_item=None,
            element=("ButtonName", "FaxDefaultApplication"),
        )
        yield OpenAPISetting(
            name="OpenApiAppSetting",
            action="OpenapiAppSetting",
            request_item=None,
            element="OpenapiAppList",
            payload="<ApplicationList>App</ApplicationList>",
        )
        yield OpenAPISetting(
            name="OpenApiAppGroupSetting",
            action="OpenapiAppSetting",
            request_item=None,
            element="OpenapiAppGroupList",
            payload="<ApplicationList>Group</ApplicationList>",
        )
        yield OpenAPISetting(
            name="PanelPatternSetting",
            action="PanelPatternSetting",
            request_item=None,
            element="PatternSetting",
        )
        yield OpenAPISetting(
            name="PrinterEncryptionSetting",
            action="PrinterEncryptionSetting",
            request_item=None,
            element="PrinterEncryptionSetting",
        )

    def enum_settings(self) -> typing.Iterable[OpenAPISetting]:
        for setting in sorted(self.settings.values()):
            if not self.filter or setting.name.casefold() in self.filter:
                yield setting

    def get_setting(self, name: str) -> OpenAPISetting | None:
        name_ignore_case = name.casefold()
        if self.filter and name_ignore_case not in self.filter:
            return None
        return self.settings.get(name_ignore_case)

    def read_setting(self, setting: OpenAPISetting) -> xml.etree.ElementTree.Element:
        element = xml.etree.ElementTree.Element(setting.name)
        response = self.request(
            f"Get{setting.action}",
            request_item=setting.request_item,
            payload=setting.payload,
            include_backup=True,
        )
        if isinstance(setting.element, str):
            matching = response.findall(setting.element)
            if len(matching) != 1:
                raise OpenAPIError(
                    f"found {len(matching)} matching <{setting.element}>",
                    response,
                )
            element.extend(matching[0])
        else:
            for name in setting.element:
                matching = response.findall(name)
                if len(matching) < 1:
                    raise OpenAPIError(
                        f"found no matching <{name}>",
                        response,
                    )
                element.extend(matching)
        return element

    def write_setting(
        self, setting: OpenAPISetting, element: xml.etree.ElementTree.Element
    ) -> None:
        if setting.readonly:
            raise ValueError(f"setting '{setting.name}' is read-only")
        if isinstance(setting.element, str):
            clone = copy.deepcopy(element)
            clone.tag = setting.element
            payload = xml.etree.ElementTree.tostring(clone, encoding="unicode")
        else:
            payload = "".join(
                xml.etree.ElementTree.tostring(child, encoding="unicode")
                for child in element
            )
        self.request(
            f"Set{setting.action}",
            request_item=setting.request_item,
            include_backup=True,
            payload=payload + setting.payload,
        )

    def read_config(self) -> xml.etree.ElementTree.Element:
        return xml.etree.ElementTree.fromstring(
            sys.stdin.read() if self.config is None else self.config.read_bytes()
        )

    def write_config(self, config: xml.etree.ElementTree.Element) -> None:
        result = xml.etree.ElementTree.tostring(
            config, encoding="utf-8", xml_declaration=True
        )
        if self.config is None:
            sys.stdout.buffer.write(result)
            sys.stdout.buffer.write(b"\n")
        else:
            self.config.write_bytes(result)

    def request(
        self,
        action: str,
        *,
        application_id: int = 0,
        management_id: int = 0,
        request_item: str | None = None,
        payload: str = "",
        include_version: bool = False,
        include_backup: bool = False,
    ) -> xml.etree.ElementTree.Element:
        payloads: list[str] = []
        if request_item is not None:
            payloads.append(f"<RequestItem>{escape(request_item)}</RequestItem>")
        if self.auth_key is not None:
            payloads.append(
                f"<OperatorInfo><AuthKey>{escape(self.auth_key)}</AuthKey></OperatorInfo>"
            )
        if self.lock_key is not None:
            payloads.append(f"<LockKey>{escape(self.lock_key)}</LockKey>")
        payloads.append(payload)
        if include_version:
            payloads.append(
                f"<Version><Major>{OPENAPI_VERSION_MAJOR}</Major><Minor>{OPENAPI_VERSION_MINOR}</Minor></Version>"
            )
        if include_backup:
            payloads.append(
                f"<BackUp>true</BackUp><BackUpPassword>MYSKIMGS</BackUpPassword>"
            )
        request_body = f"""\
<SOAP-ENV:Envelope
    xmlns:SOAP-ENV="{SOAP_NAMESPACE}"
    xmlns:SOAP-ENC="http://schemas.xmlsoap.org/soap/encoding/"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xmlns:xsd="http://www.w3.org/2001/XMLSchema"
>
    <SOAP-ENV:Header>
        <me:AppReqHeader xmlns:me="http://www.konicaminolta.com/Header/OpenAPI-{OPENAPI_VERSION_MAJOR}-{OPENAPI_VERSION_MINOR}">
            <ApplicationID>{application_id}</ApplicationID>
            <UserName>{escape(self.username)}</UserName>
            <Password>{escape(self.password)}</Password>
            <Version>
                <Major>{OPENAPI_VERSION_MAJOR}</Major>
                <Minor>{OPENAPI_VERSION_MINOR}</Minor>
            </Version>
            <AppManagementID>{management_id}</AppManagementID>
        </me:AppReqHeader>
    </SOAP-ENV:Header>
    <SOAP-ENV:Body>
        <AppReq{action} xmlns="{OPENAPI_NAMESPACE}">
            {"".join(payloads)}
        </AppReq{action}>
    </SOAP-ENV:Body>
</SOAP-ENV:Envelope>"""
        try:
            _ = xml.etree.ElementTree.fromstring(request_body)
        except xml.etree.ElementTree.ParseError as error:
            raise ValueError(f"invalid XML request:\n{request_body}") from error

        request = urllib.request.Request(
            url=self.url,
            data=request_body.encode(encoding="utf-8"),
            headers={
                "SOAPAction": f'"{OPENAPI_NAMESPACE}#AppReq{action}"',
                "Content-Type": 'text/xml; charset="utf-8"',
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, context=self.ssl_context, timeout=60
            ) as response:
                response_body = response.read()
        except urllib.error.HTTPError as error:
            error_body = error.read()
            data = error_body
            try:
                data = xml.etree.ElementTree.fromstring(error_body)
                fault = data.find(f"{{{SOAP_NAMESPACE}}}Body/{{{SOAP_NAMESPACE}}}Fault")
                if fault is not None:
                    data = fault
            except xml.etree.ElementTree.ParseError:
                pass
            raise OpenAPIError(
                f"HTTP request failed with {error.code} {error.reason}", data
            ) from error
        except urllib.error.URLError as error:
            raise OpenAPIError(f"HTTP request failed: {error.reason}") from error
        try:
            root = xml.etree.ElementTree.fromstring(response_body)
        except xml.etree.ElementTree.ParseError as error:
            raise OpenAPIError("server returned invalid XML", response_body) from error
        fault = root.find(f"{{{SOAP_NAMESPACE}}}Body/{{{SOAP_NAMESPACE}}}Fault")
        if fault is not None:
            raise OpenAPIError("SOAP request failed", fault)
        response_node = root.find(
            f"{{{SOAP_NAMESPACE}}}Body/{{{OPENAPI_NAMESPACE}}}AppRes{action}"
        )
        if response_node is None:
            raise OpenAPIError("SOAP response contains no matching body", root)
        result_node = response_node.find("Result")
        if result_node is None:
            raise OpenAPIError("Result missing in response", response_node)
        if result_node.findtext("ResultInfo") != "Ack":
            raise OpenAPIError("server did not ack", result_node)
        if request_item is not None:
            request_item_node = response_node.find("RequestItem")
            if request_item_node is None:
                raise OpenAPIError("RequestItem missing in response", response_node)
            if request_item_node.text != request_item:
                raise OpenAPIError(
                    "server returned different request item", request_item_node
                )
        return response_node

    @contextlib.contextmanager
    def authenticate(self) -> typing.Generator[None]:
        if self.auth_key is not None:
            raise RuntimeError("OpenAPI already authenticated")
        response = self.request(
            "Login",
            payload=f"<OperatorInfo><UserType>Admin</UserType><Password>{escape(self.admin_password)}</Password></OperatorInfo><TimeOut>60</TimeOut>",
        )
        auth_key = response.findtext("AuthKey")
        if not auth_key:
            raise OpenAPIError("AuthKey missing in response", response)
        self.auth_key = auth_key
        try:
            yield
        finally:
            try:
                self.request("Logout")
            finally:
                self.auth_key = None

    @contextlib.contextmanager
    def lock(self) -> typing.Generator[None]:
        if self.lock_key is not None:
            raise RuntimeError("OpenAPI already locked")
        response = self.request(
            "EnterDeviceLock",
            payload="<LockType>DownloadLock</LockType><PackageEntryFlg>Package</PackageEntryFlg><TimeOut>60</TimeOut>",
        )
        lock_key = response.findtext("LockKey")
        if not lock_key:
            raise OpenAPIError("LockKey missing in response", response)
        self.lock_key = lock_key
        try:
            yield
        finally:
            try:
                self.request("ExitDeviceLock")
            finally:
                self.lock_key = None
