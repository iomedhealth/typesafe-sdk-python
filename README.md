# TypeSafe AI Python SDK

Python SDK for [TypeSafe AI](https://typesafe.ai).

## Quickstart

Install the SDK:

```
uv add typesafe-sdk
```

Extras (for example, `typesafe-sdk[http2]`) supported:

- `http2`: enable [HTTP/2 support](https://docs.typesafe.ai/sdk/python/usage/advanced/#http2)
- `pil`: send [Pillow images](https://docs.typesafe.ai/sdk/python/api/types/assets/#typesafe_sdk.Image.from_pil)
- `zstd`: [compress request bodies](https://docs.typesafe.ai/sdk/python/usage/advanced/#ensure-zstd-is-available) on Python 3.13 and earlier

Set `TYPESAFE_API_KEY` in your environment, then instantiate and use the client:

```python
from typesafe_sdk import Choice, TypeSafeClient

with TypeSafeClient() as client:
    response = client.system_one(
        state={"document": "I was charged twice. Please fix this ASAP."},
        questions={
            "category": Choice(
                instructions="What is this ticket about?",
                criteria={"billing": None, "technical": None, "other": None},
            ),
        },
    )

print(response.choices["category"].choice)
```

learn what TypeSafe is, what it can do, and how to use it in [TypeSafe docs](https://docs.typesafe.ai/).

## Documentation

Learn more in [SDK docs](https://docs.typesafe.ai/go/python-sdk).
