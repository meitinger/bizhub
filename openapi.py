import argparse
import contextlib
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
        self.data = data

    def __str__(self) -> str:
        message = super().__str__()
        if isinstance(self.data, bytes):
            additional = self.data.decode("utf-8", errors="replace")
        elif isinstance(self.data, xml.etree.ElementTree.Element):
            xml.etree.ElementTree.indent(self.data)
            additional = xml.etree.ElementTree.tostring(self.data, encoding="unicode")
        else:
            additional = str(self.data or "")
        return f"{message}\n{additional}" if additional else message


@dataclasses.dataclass(frozen=True, order=True)
class OpenAPISetting:
    name: str
    action: str
    request_item: str
    readonly: bool
    element: str | typing.Literal["*"]


class OpenAPIArguments(typing.TypedDict):
    url: str
    config: typing.NotRequired[pathlib.Path]
    insecure: typing.NotRequired[bool]
    filter: typing.NotRequired[typing.Iterable[str]]
    password: str


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
        except ValueError as error:
            raise ValueError(f"OpenAPI URL is invalid: {error}") from error
        if parts.scheme not in {"http", "https"}:
            raise ValueError(f"unsupported OpenAPI URL scheme: {parts.scheme!r}")
        hostname = parts.hostname
        if hostname is None:
            raise ValueError("OpenAPI URL is missing a hostname")
        netloc = f"[{hostname}]" if ":" in hostname else hostname
        if parts.port is not None:
            netloc = f"{netloc}:{parts.port}"
        if args.get("insecure", False):
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
        else:
            ssl_context = None
        self._settings: typing.Final = dict[str, OpenAPISetting]()
        self.url: typing.Final = urllib.parse.urlunsplit(
            (parts.scheme, netloc, parts.path, parts.query, "")
        )
        self.username: typing.Final = urllib.parse.unquote(parts.username or "")
        self.password: typing.Final = urllib.parse.unquote(parts.password or "")
        self.admin_password: typing.Final = args["password"]
        self.config: typing.Final = args.get("config")
        self.filter: typing.Final = frozenset(args.get("filter", ()))
        self.ssl_context: typing.Final = ssl_context
        self.auth_key: str | None = None
        self.lock_key: str | None = None
        self._register_default_settings()

    def _register_default_settings(self) -> None:
        for simple_device_info_detail in (
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
            self.register_setting(
                OpenAPISetting(
                    name=simple_device_info_detail,
                    action="DeviceInfoDetail",
                    request_item=simple_device_info_detail,
                    readonly=False,
                    element=simple_device_info_detail,
                )
            )
        for readonly_device_info_detail in (
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
            self.register_setting(
                OpenAPISetting(
                    name=readonly_device_info_detail,
                    action="DeviceInfoDetail",
                    request_item=readonly_device_info_detail,
                    readonly=True,
                    element=readonly_device_info_detail,
                )
            )
        for setting_device_info_detail in (
            "Bmlinks",
            "CertificateVerification",
            "Ssdp",
            "WebDav",
            "WebService",
        ):
            self.register_setting(
                OpenAPISetting(
                    name=setting_device_info_detail,
                    action="DeviceInfoDetail",
                    request_item=setting_device_info_detail,
                    readonly=False,
                    element=f"{setting_device_info_detail}Setting",
                )
            )
        for readonly_list_device_info_detail in ("Consumable", "Version"):
            self.register_setting(
                OpenAPISetting(
                    name=readonly_list_device_info_detail,
                    action="DeviceInfoDetail",
                    request_item=readonly_list_device_info_detail,
                    readonly=True,
                    element=f"{readonly_list_device_info_detail}List",
                )
            )
        self.register_setting(
            OpenAPISetting(
                name="MarketArea",
                action="DeviceInfoDetail",
                request_item="MarketArea",
                readonly=True,
                element="*",
            )
        )
        self.register_setting(
            OpenAPISetting(
                name="PhysicalInterface",
                action="DeviceInfoDetail",
                request_item="PhysicalInterface",
                readonly=False,
                element="Physical",
            )
        )
        for simple_device_permanent_setting in (
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
            self.register_setting(
                OpenAPISetting(
                    name=simple_device_permanent_setting,
                    action="DevicePermanentSetting",
                    request_item=simple_device_permanent_setting,
                    readonly=False,
                    element=simple_device_permanent_setting,
                )
            )
        for duplicate_device_permanent_setting in ("Fax", "NetworkFax", "System"):
            self.register_setting(
                OpenAPISetting(
                    name=f"{duplicate_device_permanent_setting}Setting",
                    action="DevicePermanentSetting",
                    request_item=duplicate_device_permanent_setting,
                    readonly=False,
                    element=duplicate_device_permanent_setting,
                )
            )
        self.register_setting(
            OpenAPISetting(
                name="Authentication",
                action="DevicePermanentSetting",
                request_item="Authentication",
                readonly=False,
                element="AuthSetting",
            )
        )
        self.register_setting(
            OpenAPISetting(
                name="Tsi",
                action="DevicePermanentSetting",
                request_item="Tsi",
                readonly=False,
                element="TsiConfiguration",
            )
        )
        self.register_setting(
            OpenAPISetting(
                name="Bookmark",
                action="BrowserSetting",
                request_item="Bookmark",
                readonly=False,
                element="Bookmarklist",
            )
        )
        self.register_setting(
            OpenAPISetting(
                name="BrowserContent",
                action="BrowserSetting",
                request_item="ContentSetting",
                readonly=False,
                element="ContentSetting",
            )
        )
        self.register_setting(
            OpenAPISetting(
                name="BrowserOperation",
                action="BrowserSetting",
                request_item="OperationSetting",
                readonly=False,
                element="OperationSetting",
            )
        )

    def enum_settings(self) -> typing.Iterable[OpenAPISetting]:
        for setting in sorted(self._settings.values()):
            if not self.filter or setting.name in self.filter:
                yield setting

    def get_setting(self, name: str) -> OpenAPISetting | None:
        if self.filter and name not in self.filter:
            return None
        return self._settings.get(name)

    def register_setting(self, setting: OpenAPISetting) -> None:
        if setting.name in self._settings:
            raise ValueError(f"setting '{setting.name}' already registered")
        self._settings[setting.name] = setting

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
            raise OpenAPIError("response did not ack", result_node)
        response_node.remove(result_node)
        if request_item is not None:
            request_item_node = response_node.find("RequestItem")
            if request_item_node is None:
                raise OpenAPIError("RequestItem missing in response", response_node)
            if request_item_node.text != request_item:
                raise OpenAPIError(
                    "server returned different request item", request_item_node
                )
            response_node.remove(request_item_node)
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
            self.request("Logout")
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
            self.request("ExitDeviceLock")
            self.lock_key = None
