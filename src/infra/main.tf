terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.28.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }
}

locals {
  config = yamldecode(file("${path.module}/../../config.yaml"))

  aws_region = local.config.infrastructure.aws_region
  model_id   = local.config.agent_runtime.model_id

  agent_runtime_name = local.config.agent_runtime.agent_name
  project_name       = local.config.project.name

  max_rounds   = try(local.config.agent_runtime.max_rounds, 5)
  target_score = try(local.config.agent_runtime.target_score, 4)
  timeout      = try(local.config.agent_runtime.timeout_seconds, 900)
}

provider "aws" {
  region = local.aws_region
}

data "aws_caller_identity" "current" {}

locals {
  account_id            = data.aws_caller_identity.current.account_id
  inference_profile_arn = "arn:aws:bedrock:${local.aws_region}:${local.account_id}:inference-profile/${local.model_id}"
}

data "archive_file" "agent_bundle" {
  type        = "zip"
  source_dir  = "${path.module}/../"
  output_path = "${path.module}/agent_bundle.zip"

  excludes = [
    "infra",
    "infra/*",
    "artifacts",
    "artifacts/*",
    "__pycache__",
    "__pycache__/*",
    ".venv",
    ".venv/*",
    ".terraform",
    ".terraform/*"
  ]
}

resource "aws_s3_object" "agent_code" {
  bucket = local.config.infrastructure.agentcore_deployment.staging_bucket
  key    = "${local.config.infrastructure.agentcore_deployment.staging_prefix}/agent_bundle.zip"
  source = data.archive_file.agent_bundle.output_path
  etag   = data.archive_file.agent_bundle.output_md5
}

resource "aws_bedrockagentcore_agent_runtime" "nist_reflection_agent22" {
  agent_runtime_name = local.agent_runtime_name
  description        = "Strands NIST reflection agent deployed to Bedrock AgentCore Runtime"
  role_arn           = aws_iam_role.agentcore_execution_role.arn

  agent_runtime_artifact {
    code_configuration {
      entry_point = ["agent.py"]
      runtime     = "PYTHON_3_12"

      code {
        s3 {
          bucket = aws_s3_object.agent_code.bucket
          prefix = aws_s3_object.agent_code.key
        }
      }
    }
  }

  environment_variables = {
    BEDROCK_MODEL_ID         = local.model_id
    AWS_REGION              = local.aws_region
    AWS_DEFAULT_REGION      = local.aws_region
    MAX_REFLECTION_ROUNDS   = tostring(local.max_rounds)
    TARGET_REFLECTION_SCORE = tostring(local.target_score)
  }

  network_configuration {
    network_mode = "PUBLIC"
  }

  protocol_configuration {
    server_protocol = "HTTP"
  }

  lifecycle_configuration {
    idle_runtime_session_timeout = local.timeout
    max_lifetime                 = 28800
  }

  depends_on = [
    aws_s3_object.agent_code,
    aws_iam_role_policy.agentcore_model_access,
    aws_iam_role_policy.agentcore_s3_access,
    aws_iam_role_policy.agentcore_logs
  ]
}

resource "aws_bedrockagentcore_agent_runtime_endpoint" "default" {
  name             = "default"
  agent_runtime_id = aws_bedrockagentcore_agent_runtime.nist_reflection_agent2.agent_runtime_id
  description      = "Default endpoint for Lambda proxy invocation"
}

data "archive_file" "lambda_bundle" {
  type        = "zip"
  source_file = "${path.module}/lambda_function.py"
  output_path = "${path.module}/lambda_function.zip"
}

resource "aws_lambda_function" "api_proxy" {
  filename         = data.archive_file.lambda_bundle.output_path
  function_name    = "${local.project_name}-api-proxy"
  role             = aws_iam_role.lambda_execution_role.arn
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.11"
  source_code_hash = data.archive_file.lambda_bundle.output_base64sha256
  timeout          = local.timeout

  environment {
    variables = {
      AGENT_RUNTIME_ARN       = aws_bedrockagentcore_agent_runtime.nist_reflection_agent2.agent_runtime_arn
      AGENT_RUNTIME_QUALIFIER = aws_bedrockagentcore_agent_runtime_endpoint.default.name
    }
  }

  depends_on = [
    aws_iam_role_policy.lambda_logs,
    aws_iam_role_policy.lambda_agentcore_invocation
  ]
}

resource "aws_iam_role" "agentcore_execution_role" {
  name = "terraform-reflection-agent-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "AssumeRolePolicy"
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "bedrock-agentcore.amazonaws.com"
      }
      Condition = {
        StringEquals = {
          "aws:SourceAccount" = local.account_id
        }
        ArnLike = {
          "aws:SourceArn" = "arn:aws:bedrock-agentcore:${local.aws_region}:${local.account_id}:*"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "agentcore_logs" {
  name = "terraform-cybersecurity-agentcore-logs-policy"
  role = aws_iam_role.agentcore_execution_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowAgentCoreLogGroups"
        Effect = "Allow"
        Action = [
          "logs:DescribeLogStreams",
          "logs:CreateLogGroup"
        ]
        Resource = "arn:aws:logs:${local.aws_region}:${local.account_id}:log-group:/aws/bedrock-agentcore/runtimes/*"
      },
      {
        Sid      = "AllowDescribeLogGroups"
        Effect   = "Allow"
        Action   = ["logs:DescribeLogGroups"]
        Resource = "arn:aws:logs:${local.aws_region}:${local.account_id}:log-group:*"
      },
      {
        Sid    = "AllowAgentCoreLogStreams"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:${local.aws_region}:${local.account_id}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*"
      }
    ]
  })
}

resource "aws_iam_role_policy" "agentcore_s3_access" {
  name = "terraform-cybersecurity-agentcore-s3-code-access-policy"
  role = aws_iam_role.agentcore_execution_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "AllowReadAgentBundle"
      Effect = "Allow"
      Action = [
        "s3:GetObject",
        "s3:GetObjectVersion"
      ]
      Resource = "arn:aws:s3:::${aws_s3_object.agent_code.bucket}/${aws_s3_object.agent_code.key}"
    }]
  })
}

resource "aws_iam_role_policy" "agentcore_model_access" {
  name = "terraform-cybersecurity-agentcore-model-access-policy"
  role = aws_iam_role.agentcore_execution_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowInvokeInferenceProfile"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream"
        ]
        Resource = [
          local.inference_profile_arn,
          "arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-6*"
        ]
      },
      {
        Sid      = "AllowReadInferenceProfile"
        Effect   = "Allow"
        Action   = ["bedrock:GetInferenceProfile"]
        Resource = local.inference_profile_arn
      }
    ]
  })
}

resource "aws_iam_role" "lambda_execution_role" {
  name = "terraform-cybersecurity-lambda-proxy-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_role_policy" "lambda_logs" {
  name = "terraform-cybersecurity-lambda-logs-policy"
  role = aws_iam_role.lambda_execution_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "AllowLambdaLogging"
      Effect = "Allow"
      Action = [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ]
      Resource = "arn:aws:logs:${local.aws_region}:${local.account_id}:*"
    }]
  })
}

resource "aws_iam_role_policy" "lambda_agentcore_invocation" {
  name = "terraform-cybersecurity-lambda-agentcore-invocation-policy"
  role = aws_iam_role.lambda_execution_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "AllowInvokeAgentCoreRuntime"
      Effect = "Allow"
      Action = [
        "bedrock-agentcore:InvokeAgentRuntime"
      ]
      Resource = [
        aws_bedrockagentcore_agent_runtime.nist_reflection_agent2.agent_runtime_arn,
        "${aws_bedrockagentcore_agent_runtime.nist_reflection_agent2.agent_runtime_arn}/*"
      ]
    }]
  })
}

output "agent_runtime_id" {
  value       = aws_bedrockagentcore_agent_runtime.nist_reflection_agent2.agent_runtime_id
  description = "Bedrock AgentCore Runtime ID"
}

output "agent_runtime_arn" {
  value       = aws_bedrockagentcore_agent_runtime.nist_reflection_agent2.agent_runtime_arn
  description = "Bedrock AgentCore Runtime ARN"
}

output "agent_runtime_endpoint_name" {
  value       = aws_bedrockagentcore_agent_runtime_endpoint.default.name
  description = "AgentCore Runtime endpoint qualifier/name"
}

output "lambda_function_name" {
  value       = aws_lambda_function.api_proxy.function_name
  description = "Lambda proxy function name"
}