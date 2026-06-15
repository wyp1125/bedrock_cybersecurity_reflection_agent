# infra_test/main.tf corrected 1
terraform {
  required_version = ">= 1.5.7"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
  backend "s3" {} # Backend config injected dynamically by GitHub Actions via -backend-config
}

provider "aws" {
  region = "us-east-1"
}

# -----------------------------------------------------------------------------
# 1. Identity & Access Management (IAM) Execution Roles
# -----------------------------------------------------------------------------

# Execution Role for the Canary Lambda Function
resource "aws_iam_role" "lambda_role" {
  name = "terraform-canary-lambda-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

# Policy allowing Lambda to invoke Bedrock Agents and log execution outputs
resource "aws_iam_role_policy" "lambda_policy" {
  name = "terraform-canary-lambda-execution-policy"
  role = aws_iam_role.lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Effect   = "Allow"
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Action   = [ "bedrock:InvokeAgent" ]
        Effect   = "Allow"
        # FIXED: Explicitly added the agent-alias resource path pattern matching to resolve AccessDeniedException
        Resource = [
          "arn:aws:bedrock:us-east-1:925680695682:agent/*",
          "arn:aws:bedrock:us-east-1:925680695682:agent-alias/*"
        ]
      }
    ]
  })
}

# Execution Trust Role for Amazon Bedrock Agent Service
resource "aws_iam_role" "agent_role" {
  name = "terraform-cybersecurity-agent-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "bedrock.amazonaws.com" }
      Condition = {
        StringEquals = {
          "aws:SourceAccount" = "925680695682"
        }
      }
    }]
  })
}

# Policy allowing Bedrock Agent to read and invoke the Cross-Region Inference Profile
resource "aws_iam_role_policy" "agent_inference_profile_access" {
  name = "terraform-cybersecurity-agent-inference-profile-access"
  role = aws_iam_role.agent_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "InferenceProfileAccess"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream"
        ]
        # FIXED: Removed account ID to accurately match AWS system-defined profile ARN routing
        Resource = "arn:aws:bedrock:us-east-1:925680695682:inference-profile/us.anthropic.claude-sonnet-4-6"
      },
      {
        Sid    = "InferenceProfileRead"
        Effect = "Allow"
        Action = [
          "bedrock:GetInferenceProfile"
        ]
        # FIXED: Removed account ID here as well
        Resource = "arn:aws:bedrock:us-east-1:925680695682:inference-profile/us.anthropic.claude-sonnet-4-6"
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# 2. Foundation AI Layer - Amazon Bedrock Agent Setup
# -----------------------------------------------------------------------------

resource "aws_bedrockagent_agent" "test_agent" {
  agent_name                  = "cybersecurity-reflection-agent-core"
  agent_resource_role_arn     = aws_iam_role.agent_role.arn
  foundation_model            = "us.anthropic.claude-sonnet-4-6"
  instruction                 = "You are an elite cybersecurity agent executing deep reflection. Analyze system architecture vectors systematically."
  prepare_agent               = true
  idle_session_ttl_in_seconds = 600
}

# -----------------------------------------------------------------------------
# 3. Serverless Integration Layer - AWS Lambda Setup
# -----------------------------------------------------------------------------

# Automatically bundles the local index.py file into an uploadable zip structure
data "archive_file" "lambda_zip" {
  type        = "zip"
  source_file = "${path.module}/index.py"
  output_path = "${path.module}/lambda_function.zip"
}

resource "aws_lambda_function" "test_lambda" {
  filename         = data.archive_file.lambda_zip.output_path
  function_name    = "terraform-pipeline-canary-test"
  role             = aws_iam_role.lambda_role.arn
  handler          = "index.handler"
  runtime          = "python3.12"
  timeout          = 30

  # Forces compilation tracking. Re-zips and patches the Lambda whenever index.py changes
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256

  environment {
    variables = {
      BEDROCK_AGENT_ID       = aws_bedrockagent_agent.test_agent.id
      BEDROCK_AGENT_ALIAS_ID = "TSTALIASID"
    }
  }
}

# -----------------------------------------------------------------------------
# 4. Outputs For Script Targeting
# -----------------------------------------------------------------------------

output "lambda_function_name" {
  value       = aws_lambda_function.test_lambda.function_name
  description = "The target endpoint name used by local_test.py invocation routines"
}

output "agent_id" {
  value       = aws_bedrockagent_agent.test_agent.id
  description = "The raw 10-character alphanumeric identifier for the Bedrock Agent shell"
}