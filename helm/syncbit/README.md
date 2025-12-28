# SyncBit Helm Chart

A Helm chart for deploying SyncBit - Fitbit to Victoria Metrics data synchronization service.

## Prerequisites

- Kubernetes 1.19+
- Helm 3.x
- Persistent Volume provisioner (if using persistence)
- External Secrets Operator (optional, for production deployments)

## Installing the Chart

### Basic Installation (Development)

For development or testing with Kubernetes Secrets:

```bash
helm install syncbit ./helm/syncbit \
  --set secrets.fitbitClientId="your-client-id" \
  --set secrets.fitbitClientSecret="your-client-secret" \
  --set secrets.victoriaEndpoint="https://victoria-metrics.example.com/api/v1/import/prometheus" \
  --set secrets.victoriaUser="your-username" \
  --set secrets.victoriaPassword="your-password"
```

### Production Installation (with External Secrets Operator)

For production deployments using External Secrets Operator:

```bash
helm install syncbit ./helm/syncbit \
  --set externalSecrets.enabled=true \
  --set externalSecrets.secretStore.name="1password-store" \
  --set externalSecrets.secretStore.provider="onepassword"
```

## OAuth2 Authorization

After installation, you must authorize SyncBit to access your Fitbit data:

```bash
# Get the pod name
kubectl get pods -l app.kubernetes.io/name=syncbit

# Run authorization
kubectl exec -it <pod-name> -- python main.py --authorize

# Follow the browser prompts to complete authorization
```

The authorization tokens will be stored in the persistent volume.

## Configuration

The following table lists the configurable parameters of the SyncBit chart and their default values.

### Image Configuration

| Parameter | Description | Default |
|-----------|-------------|---------|
| `image.repository` | Container image repository | `ghcr.io/origox/syncbit` |
| `image.tag` | Container image tag | `""` (uses chart appVersion) |
| `image.pullPolicy` | Image pull policy | `IfNotPresent` |
| `imagePullSecrets` | Image pull secrets | `[]` |

### Deployment Configuration

| Parameter | Description | Default |
|-----------|-------------|---------|
| `replicaCount` | Number of replicas | `1` |
| `nameOverride` | Override the name of the chart | `""` |
| `fullnameOverride` | Override the full name | `""` |

### Service Account

| Parameter | Description | Default |
|-----------|-------------|---------|
| `serviceAccount.create` | Create service account | `true` |
| `serviceAccount.annotations` | Service account annotations | `{}` |
| `serviceAccount.name` | Service account name | `""` (generated) |

### Security Context

| Parameter | Description | Default |
|-----------|-------------|---------|
| `podSecurityContext.runAsNonRoot` | Run as non-root user | `true` |
| `podSecurityContext.runAsUser` | User ID | `1000` |
| `podSecurityContext.runAsGroup` | Group ID | `1000` |
| `podSecurityContext.fsGroup` | Filesystem group | `1000` |
| `securityContext.allowPrivilegeEscalation` | Allow privilege escalation | `false` |
| `securityContext.capabilities.drop` | Dropped capabilities | `["ALL"]` |

### Resources

| Parameter | Description | Default |
|-----------|-------------|---------|
| `resources.limits.cpu` | CPU limit | `500m` |
| `resources.limits.memory` | Memory limit | `256Mi` |
| `resources.requests.cpu` | CPU request | `100m` |
| `resources.requests.memory` | Memory request | `128Mi` |

### Persistence

| Parameter | Description | Default |
|-----------|-------------|---------|
| `persistence.enabled` | Enable persistent volume | `true` |
| `persistence.storageClassName` | Storage class name | `""` (default) |
| `persistence.accessModes` | Access modes | `["ReadWriteOnce"]` |
| `persistence.size` | Volume size | `1Gi` |
| `persistence.annotations` | PVC annotations | `{}` |

### External Secrets Operator

| Parameter | Description | Default |
|-----------|-------------|---------|
| `externalSecrets.enabled` | Enable ESO integration | `false` |
| `externalSecrets.refreshInterval` | Secret refresh interval | `1h` |
| `externalSecrets.secretStore.name` | SecretStore name | `1password-store` |
| `externalSecrets.secretStore.kind` | SecretStore kind | `SecretStore` |
| `externalSecrets.secretStore.provider` | Provider type | `onepassword` |
| `externalSecrets.secrets.fitbitClientId` | Fitbit Client ID path | `op://kubernetes/syncbit/FITBIT_CLIENT_ID` |
| `externalSecrets.secrets.fitbitClientSecret` | Fitbit Client Secret path | `op://kubernetes/syncbit/FITBIT_CLIENT_SECRET` |
| `externalSecrets.secrets.victoriaUser` | Victoria user path | `op://kubernetes/victoria-metrics/USER` |
| `externalSecrets.secrets.victoriaPassword` | Victoria password path | `op://kubernetes/victoria-metrics/PASSWORD` |
| `externalSecrets.secrets.victoriaEndpoint` | Victoria endpoint path | `op://kubernetes/victoria-metrics/ENDPOINT` |

### Kubernetes Secrets (when ESO is disabled)

| Parameter | Description | Default |
|-----------|-------------|---------|
| `secrets.fitbitClientId` | Fitbit OAuth Client ID | `""` |
| `secrets.fitbitClientSecret` | Fitbit OAuth Client Secret | `""` |
| `secrets.victoriaUser` | Victoria Metrics username | `""` |
| `secrets.victoriaPassword` | Victoria Metrics password | `""` |
| `secrets.victoriaEndpoint` | Victoria Metrics endpoint | `""` |

### Application Configuration

| Parameter | Description | Default |
|-----------|-------------|---------|
| `config.syncIntervalMinutes` | Sync interval in minutes | `15` |
| `config.logLevel` | Log level | `INFO` |
| `config.fitbitRedirectUri` | OAuth redirect URI | `http://localhost:8080/callback` |
| `config.fitbitUserId` | User ID for metric labels | `default` |
| `config.backfillStartDate` | Backfill start date | `2025-06-01` |

### Probes

| Parameter | Description | Default |
|-----------|-------------|---------|
| `livenessProbe.enabled` | Enable liveness probe | `true` |
| `livenessProbe.initialDelaySeconds` | Initial delay | `30` |
| `livenessProbe.periodSeconds` | Period | `30` |
| `readinessProbe.enabled` | Enable readiness probe | `true` |
| `readinessProbe.initialDelaySeconds` | Initial delay | `10` |
| `readinessProbe.periodSeconds` | Period | `10` |

## Examples

### Custom Resource Limits

```bash
helm install syncbit ./helm/syncbit \
  --set resources.limits.cpu=1000m \
  --set resources.limits.memory=512Mi \
  --set resources.requests.cpu=200m \
  --set resources.requests.memory=256Mi
```

### Custom Storage Class

```bash
helm install syncbit ./helm/syncbit \
  --set persistence.storageClassName=fast-ssd \
  --set persistence.size=5Gi
```

### Debug Mode

```bash
helm install syncbit ./helm/syncbit \
  --set config.logLevel=DEBUG
```

### Using Specific Version

```bash
helm install syncbit ./helm/syncbit \
  --set image.tag=v1.2.0
```

## Upgrading

To upgrade an existing release:

```bash
helm upgrade syncbit ./helm/syncbit
```

## Uninstalling

To uninstall/delete the deployment:

```bash
helm uninstall syncbit
```

**Note:** The PersistentVolumeClaim is not deleted automatically. To delete it:

```bash
kubectl delete pvc syncbit
```

## Troubleshooting

### Check Pod Status

```bash
kubectl get pods -l app.kubernetes.io/name=syncbit
kubectl describe pod <pod-name>
```

### View Logs

```bash
kubectl logs -f deployment/syncbit
```

### Check Configuration

```bash
kubectl get configmap syncbit -o yaml
kubectl get secret syncbit-secrets -o yaml  # or syncbit-external
```

### Verify Persistence

```bash
kubectl get pvc syncbit
kubectl describe pvc syncbit
```

### Test Authorization

```bash
kubectl exec -it deployment/syncbit -- ls -la /app/data/
```

## Architecture

The chart deploys:

- **Deployment**: Single replica (due to token file storage)
- **ServiceAccount**: For pod identity
- **ConfigMap**: Application configuration
- **Secret** or **ExternalSecret**: Sensitive credentials
- **PersistentVolumeClaim**: Token and state storage

## Security Considerations

- Runs as non-root user (UID 1000)
- Drops all capabilities
- Read-only root filesystem disabled (application writes to /app/data)
- SecurityContext configured for minimal privileges
- Secrets mounted as files at /run/secrets/

## Development

To test the chart locally with `helm template`:

```bash
helm template syncbit ./helm/syncbit \
  --set secrets.fitbitClientId=test \
  --set secrets.fitbitClientSecret=test \
  --set secrets.victoriaEndpoint=test \
  --set secrets.victoriaUser=test \
  --set secrets.victoriaPassword=test
```

To validate the chart:

```bash
helm lint ./helm/syncbit
```

## License

MIT

## Maintainers

- origox (https://github.com/origox)
