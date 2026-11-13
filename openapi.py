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


class OpenAPIError(RuntimeError):
    def __init__(
        self, msg: str, data: bytes | xml.etree.ElementTree.Element | None = None
    ) -> None:
        super().__init__(msg)
        self.data = data

    def __str__(self) -> str:
        if isinstance(self.data, bytes):
            additional = self.data.decode("utf-8", errors="replace")
        elif isinstance(self.data, xml.etree.ElementTree.Element):
            xml.etree.ElementTree.indent(self.data)
            additional = xml.etree.ElementTree.tostring(self.data, encoding="unicode")
        else:
            additional = str(self.data)
        return f"{super().__str__()}\n{additional}\n"


class _OpenAPIRequestItem(typing.TypedDict):
    action: str
    request_item: typing.NotRequired[str]
    readonly: typing.NotRequired[typing.Literal[True]]
    element: typing.NotRequired[str]


@dataclasses.dataclass(frozen=True)
class OpenAPISetting:
    name: str
    action: str
    request_item: str
    readonly: bool
    element: str | typing.Literal["*"]


SOAP_NAMESPACE = "http://schemas.xmlsoap.org/soap/envelope/"
OPENAPI_VERSION_MAJOR = 7
OPENAPI_VERSION_MINOR = 34
OPENAPI_NAMESPACE = f"http://www.konicaminolta.com/service/OpenAPI-{OPENAPI_VERSION_MAJOR}-{OPENAPI_VERSION_MINOR}"
_OPENAPI_REQUEST_ITEMS: dict[str, _OpenAPIRequestItem] = {
    # Browser
    "BrowserBookmark": {
        "action": "BrowserSetting",
        "request_item": "Bookmark",
        "element": "Bookmarklist",
    },
    "BrowserContent": {
        "action": "BrowserSetting",
        "request_item": "ContentSetting",
    },
    "BrowserOperation": {
        "action": "BrowserSetting",
        "request_item": "OperationSetting",
    },
    # DeviceInfoDetail
    "AirPrint": {"action": "DeviceInfoDetail"},
    "AppleTalk": {"action": "DeviceInfoDetail"},
    "Bmlinks": {
        "action": "DeviceInfoDetail",
        "element": "BmlinksSetting",
    },
    "Bonjour": {"action": "DeviceInfoDetail"},
    "CertificateVerification": {
        "action": "DeviceInfoDetail",
        "element": "CertificateVerificationSetting",
    },
    "Consumable": {
        "action": "DeviceInfoDetail",
        "readonly": True,
        "element": "ConsumableList",
    },
    "ExpansionNetwork": {"action": "DeviceInfoDetail"},
    "Fax": {
        "action": "DeviceInfoDetail",
        "readonly": True,
    },
    "Filtering": {"action": "DeviceInfoDetail"},
    "FtpClient": {"action": "DeviceInfoDetail"},
    "Ftpd": {"action": "DeviceInfoDetail"},
    "FwRollback": {
        "action": "DeviceInfoDetail",
        "readonly": True,
    },
    "Http": {"action": "DeviceInfoDetail"},
    "Ieee8021x": {"action": "DeviceInfoDetail"},
    "Iisw": {"action": "DeviceInfoDetail"},
    "Input": {"action": "DeviceInfoDetail"},
    "Ipp": {"action": "DeviceInfoDetail"},
    "Ldap": {"action": "DeviceInfoDetail"},
    "Lpr": {"action": "DeviceInfoDetail"},
    "MailReceive": {"action": "DeviceInfoDetail"},
    "MailReceiveSmtp": {"action": "DeviceInfoDetail"},
    "MailSend": {"action": "DeviceInfoDetail"},
    "Marker": {
        "action": "DeviceInfoDetail",
        "readonly": True,
    },
    "MarketArea": {
        "action": "DeviceInfoDetail",
        "readonly": True,
        "element": "*",
    },
    "MediaPath": {
        "action": "DeviceInfoDetail",
        "readonly": True,
    },
    "Netware": {"action": "DeviceInfoDetail"},
    "NetworkFax": {"action": "DeviceInfoDetail"},
    "Ntp": {"action": "DeviceInfoDetail"},
    "Odh": {
        "action": "DeviceInfoDetail",
        "readonly": True,
    },
    "OpenApi": {"action": "DeviceInfoDetail"},
    "Output": {
        "action": "DeviceInfoDetail",
        "readonly": True,
    },
    "PdlInfoList": {
        "action": "DeviceInfoDetail",
        "readonly": True,
    },
    "PhysicalInterface": {
        "action": "DeviceInfoDetail",
        "element": "Physical",
    },
    "RawPort": {"action": "DeviceInfoDetail"},
    "RemoteFwUpdate": {"action": "DeviceInfoDetail"},
    "ReportPrintList": {
        "action": "DeviceInfoDetail",
        "readonly": True,
    },
    "Sensor": {
        "action": "DeviceInfoDetail",
        "readonly": True,
    },
    "Slp": {"action": "DeviceInfoDetail"},
    "Smime": {"action": "DeviceInfoDetail"},
    "Snmp": {"action": "DeviceInfoDetail"},
    "Ssdp": {
        "action": "DeviceInfoDetail",
        "element": "SsdpSetting",
    },
    "Storage": {
        "action": "DeviceInfoDetail",
        "readonly": True,
    },
    "System": {"action": "DeviceInfoDetail"},
    "SystemConnection": {"action": "DeviceInfoDetail"},
    "TcpipGeneral": {"action": "DeviceInfoDetail"},
    "TcpSocket": {"action": "DeviceInfoDetail"},
    "Version": {
        "action": "DeviceInfoDetail",
        "readonly": True,
        "element": "VersionList",
    },
    "VLAN": {"action": "DeviceInfoDetail"},
    "WebDav": {
        "action": "DeviceInfoDetail",
        "element": "WebDavSetting",
    },
    "WebService": {
        "action": "DeviceInfoDetail",
        "element": "WebServiceSetting",
    },
    "WindowsNetwork": {"action": "DeviceInfoDetail"},
    # DevicePermanentSetting
    "AcquirePermission": {"action": "DevicePermanentSetting"},
    "Admin": {"action": "DevicePermanentSetting"},
    "AdminMyPanel": {"action": "DevicePermanentSetting"},
    "Alert": {"action": "DevicePermanentSetting"},
    "AuthDevice": {"action": "DevicePermanentSetting"},
    "Authentication": {
        "action": "DevicePermanentSetting",
        "element": "AuthSetting",
    },
    "Box": {"action": "DevicePermanentSetting"},
    "CloudConnection": {"action": "DevicePermanentSetting"},
    "CommonPaperHandling": {"action": "DevicePermanentSetting"},
    "Copy": {"action": "DevicePermanentSetting"},
    "Counter": {"action": "DevicePermanentSetting"},
    "CounterReport": {"action": "DevicePermanentSetting"},
    "CustomPanel": {"action": "DevicePermanentSetting"},
    "CustomPaper": {"action": "DevicePermanentSetting"},
    "DateTime": {"action": "DevicePermanentSetting"},
    "ExpertAdjust": {"action": "DevicePermanentSetting"},
    "Fax": {"action": "DevicePermanentSetting"},
    "HeaderFooter": {"action": "DevicePermanentSetting"},
    "HeaderFooterForPanel": {"action": "DevicePermanentSetting"},
    "Ifax": {"action": "DevicePermanentSetting"},
    "InternalWebServer": {"action": "DevicePermanentSetting"},
    "LightPanelTopMenu": {"action": "DevicePermanentSetting"},
    "MfpService": {"action": "DevicePermanentSetting"},
    "Network": {"action": "DevicePermanentSetting"},
    "NetworkFax": {"action": "DevicePermanentSetting"},
    "OperationLevel": {"action": "DevicePermanentSetting"},
    "Panel": {"action": "DevicePermanentSetting"},
    "PanelTopMenu": {"action": "DevicePermanentSetting"},
    "Power": {"action": "DevicePermanentSetting"},
    "Print": {"action": "DevicePermanentSetting"},
    "Pswc": {"action": "DevicePermanentSetting"},
    "ReferLicence": {"action": "DevicePermanentSetting"},
    "Scan": {"action": "DevicePermanentSetting"},
    "SearchOption": {"action": "DevicePermanentSetting"},
    "Security": {"action": "DevicePermanentSetting"},
    "SingleSignOnDomain": {"action": "DevicePermanentSetting"},
    "SingleSignOnService": {"action": "DevicePermanentSetting"},
    "SIPSetting": {"action": "DevicePermanentSetting"},
    "Size": {"action": "DevicePermanentSetting"},
    "System": {"action": "DevicePermanentSetting"},
    "Tsi": {
        "action": "DevicePermanentSetting",
        "element": "TsiConfiguration",
    },
    "UbiquitousPrint": {"action": "DevicePermanentSetting"},
    "WeeklyTimer": {"action": "DevicePermanentSetting"},
}
_OPENAPI_SETTINGS: dict[str, OpenAPISetting] = {
    name: OpenAPISetting(
        name=name,
        action=item["action"],
        request_item=item.get("request_item", name),
        readonly=item.get("readonly", False),
        element=item.get("element", item.get("request_item", name)),
    )
    for (name, item) in _OPENAPI_REQUEST_ITEMS.items()
}


class _Args(argparse.Namespace):
    openapi: str
    config: pathlib.Path | None
    insecure: bool
    filter: list[str]
    password: str | None


class OpenAPI:
    def __init__(self):
        parser = argparse.ArgumentParser()
        parser.add_argument("openapi", help="OpenAPI URL")
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
            choices=_OPENAPI_SETTINGS.keys(),
            help="settings to handle (default: all)",
        )
        parser.add_argument("--password", help="admin password (default: prompt)")
        args = parser.parse_args(namespace=_Args())
        try:
            parts = urllib.parse.urlsplit(args.openapi)
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
        self.url = urllib.parse.urlunsplit(
            (
                parts.scheme,
                netloc,
                parts.path,
                parts.query,
                "",
            )
        )
        self.username = urllib.parse.unquote(parts.username or "")
        self.password = urllib.parse.unquote(parts.password or "")
        self.admin_password = (
            getpass.getpass("Password: ") if args.password is None else args.password
        )
        if args.insecure:
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
        else:
            ssl_context = None
        self.config = args.config
        self.filter = args.filter
        self.ssl_context = ssl_context
        self.auth_key: str | None = None
        self.lock_key: str | None = None

    def settings(self) -> typing.Iterable[OpenAPISetting]:
        if not self.filter:
            return _OPENAPI_SETTINGS.values()
        return (
            setting
            for setting in _OPENAPI_SETTINGS.values()
            if setting.name in self.filter
        )

    def setting_by_name(self, name: str) -> OpenAPISetting | None:
        if self.filter and name not in self.filter:
            return None
        return _OPENAPI_SETTINGS.get(name)

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
    def authenticate(self):
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
            yield None
        finally:
            self.request("Logout")
            self.auth_key = None

    @contextlib.contextmanager
    def lock(self):
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
            yield None
        finally:
            self.request("ExitDeviceLock")
            self.lock_key = None
