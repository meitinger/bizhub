import openapi
import sys
import xml.etree.ElementTree

if __name__ == "__main__":
    api = openapi.OpenAPI(**openapi.parse_args())
    config = api.read_config()
    with api.authenticate():
        with api.lock():
            for element in config:
                setting = api.get_setting(element.tag)
                if setting is None or setting.readonly:
                    continue
                try:
                    if setting.element == "*":
                        payload = "".join(
                            xml.etree.ElementTree.tostring(child, encoding="unicode")
                            for child in element
                        )
                    else:
                        element.tag = setting.element
                        payload = xml.etree.ElementTree.tostring(
                            element, encoding="unicode"
                        )
                    response = api.request(
                        f"Set{setting.action}",
                        request_item=setting.request_item,
                        include_backup=True,
                        payload=payload,
                    )
                except openapi.OpenAPIError as error:
                    print(
                        f"Set{setting.action}_{setting.request_item}: {error}",
                        file=sys.stderr,
                    )
                else:
                    print(setting.name)
