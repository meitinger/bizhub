import argparse
import copy
import getpass
import pathlib
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree
from xml.sax.saxutils import escape


class Args(argparse.Namespace):
    openapi: str
    major: int
    minor: int
    out_file: pathlib.Path | None
    insecure: bool
    password: str | None


SOAP_NAMESPACE = "http://schemas.xmlsoap.org/soap/envelope/"
OAP_VERSION_MAJOR = 7
OAP_VERSION_MINOR = 34
OAP_NAMESPACE = f"http://www.konicaminolta.com/service/OpenAPI-{OAP_VERSION_MAJOR}-{OAP_VERSION_MINOR}"
OAP_METHODS: dict[str, list[str] | None] = {
    "AbbrConfiguration": None,
    "BoxConfiguration": None,
    "CopyProgramConfiguration": None,
    "ProgramKeyConfiguration": None,
    "DeviceInfo": [
        "Company",
        "ExtensionFunction",
        "Option",
        "SupportFunction",
    ],
    "DeviceInfoDetail": [
        "AirPrint",
        "AppleTalk",
        "Bmlinks",
        "Bonjour",
        "CertificateVerification",
        "Consumable",  # read-only
        "ExpansionNetwork",
        "Fax",  # read-only
        "Filtering",
        "FtpClient",
        "Ftpd",
        "FwRollback",  # read-only
        "Http",
        "Ieee8021x",
        "Input",
        "Ipp",
        "Ldap",
        "Lpr",
        "MailReceive",
        "MailReceiveSmtp",
        "MailSend",
        "Marker",  # read-only
        "MarketArea",  # read-only
        "MediaPath",  # read-only
        "Netware",
        "NetworkFax",
        "Ntp",
        "Odh",  # read-only
        "OpenApi",
        "Output",  # read-only
        "PdlInfoList",  # read-only,
        "PhysicalInterface",
        "RawPort",
        "RemoteFwUpdate",
        "ReportPrintList",  # read-only,
        "Sensor",  # read-only,
        "Slp",
        "Smime",
        "Snmp",
        "Ssdp",
        "Storage",  # read-only,
        "System",
        "SystemConnection",
        "TcpipGeneral",
        "TcpSocket",
        "Version",  # read-only,
        # write-only: VLAN
        "WebDav",
        "WebService",
        "WindowsNetwork",
    ],
    "DevicePermanentSetting": [
        "AcquirePermission",
        "Admin",
        "AdminMyPanel",
        "Alert",
        "AuthDevice",
        "Authentication",
        "Box",
        # write-only: CloudConnection
        "CommonPaperHandling",
        "Copy",
        "Counter",
        "CounterReport",
        "CustomPaper",
        "DateTime",
        # write-only: ExpertAdjust
        "Fax",
        "HeaderFooter",
        # write-only: HeaderFooterForPanel
        "Ifax",
        "InternalWebServer",
        "Network",
        "NetworkFax",
        "OperationLevel",
        "Panel",
        "PanelTopMenu",
        "Power",
        "Print",
        "Pswc",
        "ReferLicence",
        "Scan",
        # write-only: SearchOption
        "Security",
        "SingleSignOnDomain",
        "SingleSignOnService",
        # write-only: SIPSetting
        "Size",
        "System",
        "Tsi",
        "UbiquitousPrint",
        "WeeklyTimer",
    ],
}

args: Args | None = None
ssl_context: ssl.SSLContext | None = None


def merge_node(
    target: xml.etree.ElementTree.Element,
    source: xml.etree.ElementTree.Element,
) -> None:
    target_has_children = 0 < len(target)
    target_has_text = bool(target.text and target.text.strip())
    source_has_children = 0 < len(source)
    source_has_text = bool(source.text and source.text.strip())

    if target_has_children and target_has_text:
        raise RuntimeError(f"target <{target.tag}> contains text and children")

    if source_has_children and source_has_text:
        raise RuntimeError(f"source <{source.tag}> contains text and children")

    if source_has_text:
        if target_has_children:
            raise RuntimeError(
                f"cannot merge value <{source.tag}> into container <{target.tag}>"
            )

        if target_has_text:
            if target.text != source.text:
                raise RuntimeError(
                    f"value for <{source.tag}> differs: {source.text!r} != {target.text!r}"
                )
        else:
            target.text = source.text

    elif source_has_children:
        if target_has_text:
            raise RuntimeError(
                f"cannot merge container <{source.tag}> into value <{target.tag}>"
            )

        for source_child in source:
            target_matches = [
                child for child in target if child.tag == source_child.tag
            ]

            if not target_matches:
                target.append(copy.deepcopy(source_child))
            elif len(target_matches) == 1:
                merge_node(target_matches[0], source_child)
            else:
                raise RuntimeError(
                    f"multiple <{source_child.tag}> found in <{target.tag}>"
                )


def soap_request(
    action: str,
    *,
    application_id: int = 0,
    management_id: int = 0,
    payload: str = "",
    request_item: str | None = None,
    auth_key: str | None = None,
    include_version: bool = False,
    include_backup: bool = False,
) -> xml.etree.ElementTree.Element:
    if args is None:
        raise ValueError("arguments have not been initialized")
    parts = urllib.parse.urlsplit(args.openapi)
    if parts.scheme not in {"http", "https"}:
        raise ValueError(f"unsupported OpenAPI URL scheme: {parts.scheme!r}")
    hostname = parts.hostname
    if hostname is None:
        raise ValueError("OpenAPI URL is missing a hostname")
    username = urllib.parse.unquote(parts.username or "")
    password = urllib.parse.unquote(parts.password or "")
    payloads = [payload]
    if request_item is not None:
        payloads.append(f"<RequestItem>{escape(request_item)}</RequestItem>")
    if auth_key is not None:
        payloads.append(
            f"<OperatorInfo><AuthKey>{escape(auth_key)}</AuthKey></OperatorInfo>"
        )
    if include_version:
        payloads.append(
            f"<Version><Major>{OAP_VERSION_MAJOR}</Major><Minor>{OAP_VERSION_MINOR}</Minor></Version>"
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
        <me:AppReqHeader xmlns:me="http://www.konicaminolta.com/Header/OpenAPI-{OAP_VERSION_MAJOR}-{OAP_VERSION_MINOR}">
            <ApplicationID>{application_id}</ApplicationID>
            <UserName>{escape(username)}</UserName>
            <Password>{escape(password)}</Password>
            <Version>
                <Major>{OAP_VERSION_MAJOR}</Major>
                <Minor>{OAP_VERSION_MINOR}</Minor>
            </Version>
            <AppManagementID>{management_id}</AppManagementID>
        </me:AppReqHeader>
    </SOAP-ENV:Header>
    <SOAP-ENV:Body>
        <AppReq{action} xmlns="{OAP_NAMESPACE}">
            {"".join(payloads)}
        </AppReq{action}>
    </SOAP-ENV:Body>
</SOAP-ENV:Envelope>"""
    try:
        _ = xml.etree.ElementTree.fromstring(request_body)
    except xml.etree.ElementTree.ParseError as error:
        raise RuntimeError(f"invalid XML request:\n{request_body}") from error
    request_netloc = f"[{hostname}]" if ":" in hostname else hostname
    if parts.port is not None:
        request_netloc = f"{request_netloc}:{parts.port}"
    request_url = urllib.parse.urlunsplit(
        (
            parts.scheme,
            request_netloc,
            parts.path,
            parts.query,
            "",
        )
    )
    request = urllib.request.Request(
        url=request_url,
        data=request_body.encode(encoding="utf-8"),
        headers={
            "SOAPAction": f'"{OAP_NAMESPACE}#AppReq{action}"',
            "Content-Type": 'text/xml; charset="utf-8"',
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, context=ssl_context) as response:
            response_body = response.read()
    except urllib.error.HTTPError as error:
        error_body = error.read()
        message = None
        try:
            root = xml.etree.ElementTree.fromstring(error_body)
            fault = root.find(f"{{{SOAP_NAMESPACE}}}Body/{{{SOAP_NAMESPACE}}}Fault")
            if fault is not None:
                message = xml.etree.ElementTree.tostring(fault, encoding="unicode")
        except xml.etree.ElementTree.ParseError:
            pass
        if not message:
            message = error_body.decode("utf-8", errors="replace")
        raise RuntimeError(
            f"HTTP request failed with {error.code} {error.reason}:\n{message}"
        ) from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"HTTP request failed: {error.reason}") from error
    try:
        root = xml.etree.ElementTree.fromstring(response_body)
    except xml.etree.ElementTree.ParseError as error:
        message = response_body.decode("utf-8", errors="replace")
        raise RuntimeError(f"server returned invalid XML:\n{message}") from error
    fault = root.find(f"{{{SOAP_NAMESPACE}}}Body/{{{SOAP_NAMESPACE}}}Fault")
    if fault is not None:
        raise RuntimeError(xml.etree.ElementTree.tostring(fault, encoding="unicode"))
    response_node = root.find(
        f"{{{SOAP_NAMESPACE}}}Body/{{{OAP_NAMESPACE}}}AppRes{action}"
    )
    if response_node is None:
        message = xml.etree.ElementTree.tostring(root, encoding="unicode")
        raise RuntimeError(f"server did not return response:\n{message}")
    result_node = response_node.find("Result")
    if result_node is not None:
        info = result_node.findtext("ResultInfo")
        if info != "Ack":
            raise RuntimeError(f"server returned result: {info}")
        response_node.remove(result_node)
    if request_item is not None:
        request_item_node = response_node.find("RequestItem")
        if request_item_node is not None:
            if request_item_node.text != request_item:
                raise RuntimeError(
                    f"server returned different request item: {request_item_node.text}"
                )
            response_node.remove(request_item_node)
    return response_node


def login(password: str) -> str:
    response = soap_request(
        "Login",
        payload=f"<OperatorInfo><UserType>Admin</UserType><Password>{escape(password)}</Password></OperatorInfo><TimeOut>60</TimeOut>",
    )
    auth_key = response.findtext("AuthKey")
    if not auth_key:
        raise RuntimeError("missing AuthKey")
    return auth_key


def logout(auth_key: str) -> None:
    soap_request("Logout", auth_key=auth_key)


def parse_args() -> Args:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("openapi")
    parser.add_argument("--out-file", dest="out_file", type=pathlib.Path)
    parser.add_argument("--insecure", action="store_true")
    parser.add_argument("--password")
    return parser.parse_args(namespace=Args())


if __name__ == "__main__":
    args = parse_args()
    password = getpass.getpass("Password: ") if args.password is None else args.password
    if args.insecure:
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
    auth_key = login(password)
    config = xml.etree.ElementTree.Element("Configuration")
    try:
        for action, request_items in OAP_METHODS.items():
            if request_items is None:
                try:
                    merge_node(
                        config,
                        soap_request(
                            f"Get{action}", auth_key=auth_key, include_backup=True
                        ),
                    )
                except RuntimeError as error:
                    print(f"{action}: {error}", file=sys.stderr)
            else:
                for request_item in request_items:
                    try:
                        merge_node(
                            config,
                            soap_request(
                                f"Get{action}",
                                auth_key=auth_key,
                                request_item=request_item,
                                include_backup=True,
                            ),
                        )
                    except RuntimeError as error:
                        print(f"{action}[{request_item}]: {error}", file=sys.stderr)
    finally:
        logout(auth_key)
    xml.etree.ElementTree.indent(config)
    result = xml.etree.ElementTree.tostring(
        config, encoding="utf-8", xml_declaration=True
    )
    if args.out_file is None:
        sys.stdout.buffer.write(result)
        sys.stdout.buffer.write(b"\n")
    else:
        args.out_file.write_bytes(result)
