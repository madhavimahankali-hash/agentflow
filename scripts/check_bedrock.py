"""Pre-flight check for Amazon Bedrock: credentials, region, model access, tool use.

    python -m scripts.check_bedrock          # check credentials and the models in .env
    python -m scripts.check_bedrock --list   # list Anthropic models your account can use

Calls each configured model once with a tiny prompt (a fraction of a cent) and
explains the most common errors in plain words.
"""
from __future__ import annotations

import sys
import time

HINTS = {
    "AccessDeniedException": "Your IAM user/role may lack bedrock:InvokeModel, OR the model is not enabled. "
                             "Open Bedrock console -> Model access and request access for this model.",
    "ValidationException": "Bedrock rejected the request: read the message above. If it is about the model ID, "
                           "copy an ID from 'python -m scripts.check_bedrock --list'.",
    "ResourceNotFoundException": "Model not found in this region, or retired (end of life). List the models "
                                 "your account can use with: python -m scripts.check_bedrock --list",
    "UnrecognizedClientException": "AWS credentials are invalid. Run 'aws configure' or 'aws sso login'.",
    "ExpiredTokenException": "Your session expired. Run 'aws sso login' (or refresh your credentials).",
    "ThrottlingException": "Rate limited. Wait a minute and retry.",
}


def list_models(region: str) -> int:
    """Print Anthropic inference profiles and on-demand models available to this account."""
    import boto3

    bedrock = boto3.client("bedrock", region_name=region)
    print(f"Inference profiles in {region} (use these IDs in .env when present):")
    for p in bedrock.list_inference_profiles().get("inferenceProfileSummaries", []):
        if "anthropic" in p["inferenceProfileId"] and p.get("status", "ACTIVE") == "ACTIVE":
            print(f"  {p['inferenceProfileId']}")
    print("\nFoundation models with ACTIVE lifecycle:")
    for m in bedrock.list_foundation_models(byProvider="Anthropic").get("modelSummaries", []):
        if m.get("modelLifecycle", {}).get("status") == "ACTIVE":
            print(f"  {m['modelId']}")
    print("\nThen open Bedrock console -> Model access to make sure the chosen models are granted.")
    return 0


def main() -> int:
    from app.config import get_settings

    s = get_settings()
    if "--list" in sys.argv:
        return list_models(s.aws_region)
    print(f"LLM_PROVIDER = {s.llm_provider}")
    print(f"AWS_REGION   = {s.aws_region}")
    if s.llm_provider != "bedrock":
        print("\nNote: LLM_PROVIDER is not 'bedrock' in .env; the app will still run offline.")

    try:
        import boto3
        from botocore.exceptions import ClientError, NoCredentialsError
    except ImportError:
        print("boto3 is missing: pip install -r requirements.txt")
        return 1

    try:
        ident = boto3.client("sts", region_name=s.aws_region).get_caller_identity()
        principal = ident["Arn"].split(":", 5)[-1]          # e.g. user/alice or assumed-role/Dev/alice
        print(f"AWS identity = {principal}  (account id hidden)")
    except NoCredentialsError:
        print("\nFAIL: no AWS credentials found. Run 'aws configure' or 'aws sso login --profile <name>' "
              "and set AWS_PROFILE.")
        return 1
    except ClientError as e:
        code = e.response["Error"]["Code"]
        print(f"\nFAIL: {code}: {HINTS.get(code, e)}")
        return 1

    client = boto3.client("bedrock-runtime", region_name=s.aws_region)
    tool = {"toolSpec": {"name": "ping", "description": "Reply ping",
                         "inputSchema": {"json": {"type": "object", "properties": {"ok": {"type": "boolean"}}}}}}
    ok = True
    for role, model in (("supervisor", s.supervisor_model_id), ("worker", s.worker_model_id)):
        t0 = time.perf_counter()
        try:
            r = client.converse(
                modelId=model,
                messages=[{"role": "user", "content": [{"text": "Call the ping tool with ok=true."}]}],
                toolConfig={"tools": [tool]},
                inferenceConfig={"maxTokens": 50, **({"temperature": s.llm_temperature}
                                                     if s.llm_temperature is not None else {})},
            )
            ms = int((time.perf_counter() - t0) * 1000)
            used_tool = any("toolUse" in b for b in r["output"]["message"]["content"])
            u = r.get("usage", {})
            print(f"OK   {role:10} {model}  {ms} ms  tokens in/out {u.get('inputTokens')}/{u.get('outputTokens')}  "
                  f"tool use: {'yes' if used_tool else 'NO (pick a model that supports tool use)'}")
            ok &= used_tool
        except ClientError as e:
            code = e.response["Error"]["Code"]
            print(f"FAIL {role:10} {model}\n     {code}: {e.response['Error'].get('Message', '')}\n     -> "
                  f"{HINTS.get(code, 'See the AWS error above.')}")
            ok = False
    print("\nReady for: python -m eval.run_eval --provider bedrock --save" if ok else "\nFix the items above first.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
