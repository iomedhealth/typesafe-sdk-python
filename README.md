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

## Batch evaluation

Self-hosted [System One](https://docs.typesafe.ai/concepts/system-one) servers expose one extra endpoint,
`POST /v1/systemone/batch`, which evaluates up to 50 states in a single request. It is **not** part of the
public TypeSafe AI API: point `base_url` at your server before calling `system_one_batch`. Each item carries
the same `state` and `questions` as a `system_one` call (plus an optional per-item `model`), and
`results[i]` is the full `SystemOneResponse` for `requests[i]`.

<!-- skip: next -->

```python
from typesafe_sdk import Noul, TypeSafeClient

with TypeSafeClient(base_url="https://systemone.example.org") as client:
    response = client.system_one_batch(
        [
            {
                "state": {"sentence": "Niega disnea.", "entities": [{"id": "e1", "text": "disnea"}]},
                "questions": {"negated": Noul(instructions="Is `entities[0]` negated?")},
            },
            {
                "state": {"sentence": "Refiere disnea de esfuerzo.", "entities": [{"id": "e1", "text": "disnea"}]},
                "questions": {"negated": Noul(instructions="Is `entities[0]` negated?")},
            },
        ]
    )

for result in response.results:
    print(result.nouls["negated"].noul)
```

Validation mirrors `system_one`: an empty list, more than 50 items, a `None` state, or empty questions raise
`TypeSafeError` before any request is sent. The `AsyncTypeSafeClient` offers the same method as a coroutine.

## IOMED Internal Distribution

This repository is an internal fork of [TypeSafe AI SDK](https://github.com/typesafe-ai/typesafe-sdk-python) maintained by IOMED to support batch evaluation extensions and custom integrations.

### Mirror & Automation
- **Primary Repository**: Active development, code reviews, and PRs take place on GitHub (`iomedhealth/typesafe-sdk-python`).
- **GitLab Mirror**: All branches and tags are continuously pull-mirrored to GitLab at [`iomed/datascience/typesafe-sdk-python`](https://gitlab.com/iomed/datascience/typesafe-sdk-python) (Project ID: `87907153`).
- **Automated Registry Publishing**: Pushing a release tag to GitHub automatically syncs to GitLab and triggers GitLab CI to build and publish the wheel to the IOMED GitLab PyPI package registry.

### Installation

#### Local Development
```bash
pip install --extra-index-url https://gitlab.com/api/v4/projects/87907153/packages/pypi/simple typesafe-sdk
```

Or with `uv`:
```bash
uv add --extra-index-url https://gitlab.com/api/v4/projects/87907153/packages/pypi/simple typesafe-sdk
```

#### In CI / Dockerfiles
Authenticate with a GitLab token (e.g. `${CI_JOB_TOKEN}` or `${GITLAB_TOKEN}`):
```bash
pip install --extra-index-url https://gitlab-ci-token:${CI_JOB_TOKEN}@gitlab.com/api/v4/projects/87907153/packages/pypi/simple typesafe-sdk
```

### Release Workflow

To release a new version:

1. Ensure your changes are merged into `main`.
2. Create and push a signed Git tag following the `<upstream>+iomed.<revision>` convention:
   ```bash
   git checkout main
   git pull origin main
   git tag -s v0.7.5+iomed.2 -m "Release v0.7.5+iomed.2"
   git push origin v0.7.5+iomed.2
   ```
3. GitLab's pull mirror will detect the tag and automatically run the `publish-package` pipeline, building the wheel and publishing it to the registry.

## Documentation

Learn more in [SDK docs](https://docs.typesafe.ai/go/python-sdk).
