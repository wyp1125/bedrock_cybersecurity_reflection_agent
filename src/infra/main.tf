terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }
}

# 1. Parse configuration limits from the root deployment tracker
locals {
  config = yamldecode(file("${path.module}/../../config.yaml"))
}

# 2. Automatically compress the complete Strands agent src directory when code changes
data "archive_file" "agent_bundle" {
  type        = "zip"
  source_dir  = "${path.module}/../"
  output_path = "${path.module}/artifacts/agent_bundle.zip"
  excludes    = ["infra", "infra/*", "artifacts", "artifacts/*"]
}

# 3. Synchronize the build package straight to the tracking bucket
resource "aws_s3_object" "agent_code" {
  bucket = local.config.infrastructure.agentcore_deployment.staging_bucket
  key    = "${local.config.infrastructure.agentcore_deployment.staging_prefix}/agent_bundle.zip"
  source = data.archive_file.agent_bundle.output_path
  etag   = data.archive_file.agent_bundle.output_md5
}

# 4. Provision the underlying Bedrock Agent Configuration
resource "aws_bedrock_agent" "nist_reflection_agent" {
  agent_name                  = local.config.agent_runtime.agent_name
  agent_resource_role_arn     = aws_iam_role.agent_execution_role.arn
  foundation_model            = local.config.agent_runtime.model_id
  instruction                 = "Execute specialized compliance agent iterative multi-round reflection mapping workflows."
  
  # Links the deployed Strands package directly to the engine
  code_interpreter_supported  = false
  
  depends_on = [aws_s3_object.agent_code]
}

# 5. Compile the active operational version sequence code block 
resource "aws_bedrock_agent_agent_version" "latest" {
  agent_id      = aws_bedrock_agent.nist_reflection_agent.id
  description   = "Strands framework deployment synced by Terraform tracking hash: ${data.archive_file.agent_bundle.output_md5}"
}

# 6. Establish the operational pipeline testing alias endpoint
resource "aws_bedrock_agent_agent_alias" "test_alias" {
  agent_id        = aws_bedrock_agent.nist_reflection_agent.id
  agent_alias_name = "TSTALIASID"
  routing_configuration {
    agent_version = aws_bedrock_agent_agent_version.latest.version
  }
}

# 7. Package the proxy microservice logic
data "archive_file" "lambda_bundle" {
  type        = "zip"
  source_file = "${path.module}/lambda/index.py"
  output_path = "${path.module}/artifacts/lambda_function.zip"
}

# 8. Deploy the gateway proxy lambda injecting the AGENT_ID dynamically
resource "aws_lambda_function" "api_proxy" {
  filename         = data.archive_file.lambda_bundle.output_path
  function_name    = "${local.config.project.name}-api-proxy"
  role             = aws_iam_role.lambda_execution_role.arn
  handler          = "index.handler"
  runtime          = "python3.11"
  source_code_hash = data.archive_file.lambda_bundle.output_base64sha256
  timeout          = local.config.agent_runtime.timeout_seconds

  environment {
    variables = {
      # THE DYNAMIC LINK: Pulls resource identifiers directly from the newly created Bedrock block
      BEDROCK_AGENT_ID       = aws_bedrock_agent.nist_reflection_agent.id
      BEDROCK_AGENT_ALIAS_ID = aws_bedrock_agent_agent_alias.test_alias.agent_alias_id
    }
  }
}

# --- IAM Infrastructure Core Security Definitions ---

# Execution role assumed by Amazon Bedrock Agent infrastructure
resource "aws_iam_role" "agent_execution_role" {
  name = "terraform-cybersecurity-agent-execution-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "bedrock.amazonaws.com" }
    }]
  })
}

# Execution role assumed by the proxy Lambda function
resource "aws_iam_role" "lambda_execution_role" {
  name = "terraform-cybersecurity-lambda-proxy-execution-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

# Grants the agent execution role permission to call model and application inference endpoints
resource "aws_iam_role_policy" "agent_model_access" {
  name = "terraform-cybersecurity-agent-model-access-policy"
  role = aws_iam_role.agent_execution_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowStrandsToInvokeClaude"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream"
        ]
        Resource = [
          "arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-6*",
          "arn:aws:bedrock:${local.config.infrastructure.aws_region}:925680695682:inference-profile/${local.config.agent_runtime.model_id}"
        ]
      },
      {
        Sid    = "AllowStrandsToReadProfile"
        Effect = "Allow"
        Action = [
          "bedrock:GetInferenceProfile"
        ]
        Resource = "arn:aws:bedrock:${local.config.infrastructure.aws_region}:925680695682:inference-profile/${local.config.agent_runtime.model_id}"
      }
    ]
  })
}

# Grants proxy Lambda function permission to call the created Bedrock Agent instance
resource "aws_iam_role_policy" "lambda_bedrock_invocation" {
  name = "lambda-bedrock-agent-invocation-policy"
  role = aws_iam_role.lambda_execution_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["bedrock:InvokeAgent"]
      Resource = [
        "${aws_bedrock_agent.nist_reflection_agent.arn}",
        "${aws_bedrock_agent.nist_reflection_agent.arn}/*"
      ]
    }]
  })
}