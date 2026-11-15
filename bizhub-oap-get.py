import openapi
import sys
import xml.etree.ElementTree

if __name__ == "__main__":
    api = openapi.OpenAPI(**openapi.parse_args())
    config = xml.etree.ElementTree.Element("Configuration")
    with api.authenticate():
        for setting in api.enum_settings():
            element = xml.etree.ElementTree.Element(setting.name)
            try:
                response = api.request(
                    f"Get{setting.action}",
                    request_item=setting.request_item,
                    payload=setting.payload,
                    include_backup=True,
                )
                if setting.element == "*":
                    element.extend(response)
                elif isinstance(setting.element, str):
                    matching = response.findall(setting.element)
                    if len(matching) != 1:
                        raise openapi.OpenAPIError(
                            f"found {len(matching)} matching <{setting.element}>",
                            response,
                        )
                    element.extend(matching[0])
                else:
                    for name in setting.element:
                        matching = response.findall(name)
                        if len(matching) < 1:
                            raise openapi.OpenAPIError(
                                f"found no matching <{name}>",
                                response,
                            )
                        element.extend(matching)
            except openapi.OpenAPIError as error:
                print(
                    f"Get{setting.action}_{setting.request_item}: {error}",
                    file=sys.stderr,
                )
            else:
                config.append(element)
    xml.etree.ElementTree.indent(config)
    api.write_config(config)
