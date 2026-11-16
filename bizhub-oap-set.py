import openapi
import sys

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
                    api.write_setting(setting, element)
                except openapi.OpenAPIError as error:
                    print(
                        f"{setting.name}: {error}",
                        file=sys.stderr,
                    )
                else:
                    print(setting.name)
