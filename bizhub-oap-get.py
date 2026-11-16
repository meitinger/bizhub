import openapi
import sys
import xml.etree.ElementTree

if __name__ == "__main__":
    api = openapi.OpenAPI(**openapi.parse_args())
    config = xml.etree.ElementTree.Element("Configuration")
    with api.authenticate():
        for setting in api.enum_settings():
            try:
                config.append(api.read_setting(setting))
            except openapi.OpenAPIError as error:
                print(
                    f"{setting.name}: {error}",
                    file=sys.stderr,
                )
    xml.etree.ElementTree.indent(config)
    api.write_config(config)
