# SPDX-License-Identifier: Apache-2.0
"""Two fixed checkpoint identities; no paths, revisions or URLs from callers."""

import hashlib
from types import MappingProxyType

NAMES = ("original", "uncensored")
_PROFILES = MappingProxyType({
    "original": MappingProxyType({
        "name": "original",
        "pack": "models/deepseek-v4.1-flash-1017/1",
        "served_model_name": "deepseek-v4.1-flash-1017",
        "hf_repo": "deepseek-ai/DeepSeek-V4.1-Flash-1017",
        "hf_revision": "1bae0e1088993b9b82de08f59de5be5a34e42170",
        "total_bytes": 510311045295,
        "manifest_sha256": "b74ae635c35372cf6c350e4b33ef60c90ff01fe6426ad0fa7bc99b1982fe5bed",
    }),
    "uncensored": MappingProxyType({
        "name": "uncensored",
        "pack": "models/deepseek-v4.1-flash-1017-uncensored-fp8/1",
        "served_model_name": "deepseek-v4.1-flash-1017-uncensored-fp8",
        "hf_repo": "dealignai/DeepSeek-V4.1-Flash-1017-UNCENSORED-FP8",
        "hf_revision": "c19ca5ca4eaeb79d29fdf08aea8327da95b1116a",
        "total_bytes": 510311056155,
        "manifest_sha256": "5c6891a9649702b1ac0327b945600acb40c571a069942517cf14ba661a3cfcbc",
    }),
})


def select(name="original"):
    if type(name) is not str or name not in NAMES:
        raise ValueError("model profile rejected")
    return dict(_PROFILES[name])


def validate_manifest(name, raw):
    value = select(name)
    if type(raw) is not bytes or hashlib.sha256(raw).hexdigest() != value["manifest_sha256"]:
        raise ValueError("model manifest rejected")
    return value


def validate_contract(contract):
    """Absence is the original legacy schema; only the opt-in adds identity."""
    if "model_profile" not in contract:
        return select()
    value = select("uncensored")
    if (contract["model_profile"] != value
            or type(contract["model_profile"]) is not dict
            or type(contract["model_profile"].get("total_bytes")) is not int
            or contract.get("pack_revision") != value["hf_revision"]
            or contract.get("pack_bytes") != value["total_bytes"]
            or type(contract.get("pack_bytes")) is not int
            or contract.get("manifest_sha256") != value["manifest_sha256"]):
        raise ValueError("model contract rejected")
    return value
