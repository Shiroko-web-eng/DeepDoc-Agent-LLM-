# Kubernetes 部署说明

这些清单提供 Production 基线，不包含云厂商资源创建。部署前必须：

1. 将所有 `replace-with-release-digest` 替换为 CI 产生并验证过的镜像 digest。
2. 预先创建托管 PostgreSQL/pgvector、S3 Bucket、OIDC Client 和 OTLP Collector。
3. 创建 `deepdoc-secrets`，至少提供 `DEEPDOC_DATABASE_URL`、OIDC 三项、生成模型与 Embedding 的模型名/Base URL/API Key，以及 `DEEPDOC_S3_KMS_KEY_ID`；对象存储优先通过 ServiceAccount 工作负载身份授权，不写静态云密钥。
4. 先运行 `migration-job.yaml`，成功后再发布 API 和 Worker。
5. 根据实际 Ingress、DNS、数据库、S3、模型及 OTel 地址收紧 `network-policy.yaml`。示例策略只有默认拒绝与 DNS/API 入站，不能直接视为完整出口白名单。
6. 安装 KEDA PostgreSQL scaler，并确认运行身份只能读取 `durable_jobs` 队列指标。

推荐顺序：Namespace → Config/Secret/ServiceAccount → Migration Job → API → Worker → Eval Worker → NetworkPolicy。数据库备份、PITR、对象版本化、OIDC、证书和 OTel 后端属于集群外依赖，必须由 IaC 管理并在上线前完成恢复演练。
