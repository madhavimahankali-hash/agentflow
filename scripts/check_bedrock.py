"""Pre-flight check for Amazon Bedrock: credentials, region, model access, tool use.

    python -m scripts.check_bedrock

Calls each configured model once with a tiny prompt (a fraction of a cent) and
explains the most common errors in plain words.
"""
from __future__ import annotations

import sys
import time

HINTS = {
    "AccessDeniedException": "Your IAM user/role may lack bedrock:InvokeModel, OR the model is not enabled. "
                             "Open Bedrock console -> Model access and request access for this model.",
    "ValidationException": "The model ID is not valid in this region. Copy the exact model ID or inference "
                           "profile ID (often starts with 'us.' / 'eu.' / 'apac.') from the Bedrock console.",
    "ResourceNotFoundException": "Model not found in this region. Check AWS_REGION and the model ID.",
    "UnrecognizedClientException": "AWS credentials are invalid. Run 'aws configure' or 'aws sso login'.",
    "ExpiredTokenException": "Your session expired. Run 'aws sso login' (or refresh your credentials).",
    "ThrottlingException": "Rate limited. Wait a minute and retry.",
}


def main() -> int:
    from app.config import get_settings

    s = get_settings()
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
        arn = ident["Arn"]
        print(f"AWS identity = ...{arn[-40:]}  (account id hidden)")
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
                inferenceConfig={"maxTokens": 50, "temperature": 0},
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
