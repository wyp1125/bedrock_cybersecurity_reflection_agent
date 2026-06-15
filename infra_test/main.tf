# infra_test/main.tf

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Left empty intentionally. Dynamically populated at runtime via the workflow file.
  backend "s3" {}
}

# -----------------------------------------------------------------------------
# 1. Parse config.yaml Configuration File
# -----------------------------------------------------------------------------

data "local_file" "config" {
  filename = "${path.module}/../config.yaml"
}

locals {
  cfg = yamldecode(data.local_file.config.content)
  
  # Clean dynamic mapping from your configuration structure
  aws_region = local.cfg.infrastructure.aws_region
  agent_name = local.cfg.agent_runtime.agent_name
  model_id   = local.cfg.agent_runtime.model_id
}

provider "aws" {
  region = local.aws_region
}

# -----------------------------------------------------------------------------
# 2. Lambda Function & Execution Security
# -----------------------------------------------------------------------------

data "archive_file" "lambda_zip" {
  type        = "zip"
  source_file = "${path.module}/index.py"
  output_path = "${path.module}/lambda_function.zip"
}

resource "aws_iam_role" "test_lambda_role" {
  name = "cybersecurity-nist-agent-router-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "lambda_bedrock_invocation" {
  name = "cybersecurity-nist-lambda-bedrock-execution-policy"
  role = aws_iam_role.test_lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "AllowAgentInvocation"
        Effect   = "Allow"
        Action   = ["bedrock:InvokeAgent"]
        Resource = "*" 
      }
    ]
  })
}

resource "aws_lambda_function" "test_lambda" {
  filename         = data.archive_file.lambda_zip.output_path
  function_name    = "terraform-pipeline-canary-test"
  role             = aws_iam_role.test_lambda_role.arn
  handler          = "index.handler"
  runtime          = "python3.11"
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256

  environment {
    variables = {
      BEDROCK_AGENT_ID       = aws_bedrockagent_agent.test_agent.id
      BEDROCK_AGENT_ALIAS_ID = aws_bedrockagent_agent_alias.test_agent_alias.agent_alias_id
    }
  }
}

# -----------------------------------------------------------------------------
# 3. Bedrock Agent Core Configuration
# -----------------------------------------------------------------------------

resource "aws_iam_role" "bedrock_agent_service_role" {
  name = "cybersecurity-nist-agent-service-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "bedrock.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "bedrock_agent_model_access" {
  name = "cybersecurity-nist-agent-model-access-policy"
  role = aws_iam_role.bedrock_agent_service_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "BedrockModelCloudAccess"
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel"]
        Resource = "arn:aws:bedrock:${local.aws_region}::foundation-model/${local.model_id}"
      }
    ]
  })
}

resource "aws_bedrockagent_agent" "test_agent" {
  agent_name                  = "${local.agent_name}-canary"
  agent_resource_role_arn     = aws_iam_role.bedrock_agent_service_role.arn
  foundation_model            = local.model_id
  instruction                 = "You are a specialized cybersecurity assistant trained on NIST frameworks. Provide concise advice."
  idle_session_ttl_in_seconds = 600
  prepare_agent               = true  # Automatically creates and builds the working agent snapshot
}

# FIXED: Set agent_version directly to "DRAFT" inside the routing configuration block
resource "aws_bedrockagent_agent_alias" "test_agent_alias" {
  agent_alias_name = "canary-active-routing-alias"
  agent_id         = aws_bedrockagent_agent.test_agent.id

  routing_configuration {
    agent_version = "DRAFT"
  }
}

# -----------------------------------------------------------------------------
# 4. Outputs For Script Targeting
# -----------------------------------------------------------------------------

output "lambda_function_name" {
  value = aws_lambda_function.test_lambda.function_name
}