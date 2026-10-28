# SPDX-License-Identifier: Apache-2.0
"""Closed official-model identity for fresh quality calibration receipts."""

import deepseek_model_profile as models

ORIGINAL_MODEL = "deepseek/deepseek-v4.1-flash-1017"
ORIGINAL_ENDPOINT = "nib.quality-calibration-endpoint/v2"
ORIGINAL_RECEIPT = "nib.quality-calibration/v4"


def original_fields():
    return {"model": ORIGINAL_MODEL, "serving_profile": "original-long1m-graphs",
            "model_profile": models.select("original")}


def validate_original(value):
    fields = original_fields()
    if type(value) is not dict or any(value.get(key) != item for key, item in fields.items()):
        raise ValueError("Official calibration identity rejected")
    return fields


def receipt_fields(manifest):
    if manifest.get("schema") != ORIGINAL_RECEIPT:
        # Older receipts have their historical meaning; never infer a revision.
        return {}
    return validate_original(manifest)
