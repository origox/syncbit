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

For production deployments using External Secrets Operator with 1Password Connect:

```bash
helm install syncbit ./helm/syncbit \
  --set externalSecrets.enabled=true \
  --set externalSecrets.secretStore.name="onepassword" \
  --set externalSecrets.secretStore.kind="ClusterSecretStore"
```

**Note:** This assumes you have:
- External Secrets Operator installed in your cluster
- A ClusterSecretStore named `onepassword` configured for 1Password Connect
- 1Password items named `syncbit` and `victoria-metrics` in the `kubernetes` vault

## OAuth2 Authorization

After installation, you must authorize SyncBit to access your Fitbit data. There are two methods:

### Method 1: Using the Authorization Job (Recommended)

Create a Kubernetes Job to handle authorization:

```bash
# Create the authorization job
helm upgrade syncbit ./helm/syncbit \
  --reuse-values \
  --set authorization.createJob=true

# Watch the job pod start
kubectl get pods -l app.kubernetes.io/component=authorization

# Port-forward to access the OAuth callback server
kubectl port-forward job/syncbit-authorize 8080:8080

# Follow the authorization URL in the pod logs
kubectl logs -f job/syncbit-authorize

# Open the URL in your browser and complete the OAuth flow
```

After authorization is complete, disable the job and scale up the deployment:

```bash
# Remove the job
helm upgrade syncbit ./helm/syncbit \
  --reuse-values \
  --set authorization.createJob=false

# Scale up the deployment
kubectl scale deployment syncbit --replicas=1
```

### Method 2: Local Authorization with Docker

Authorize locally and upload tokens to the cluster:

```bash
# Run authorization locally with Docker
docker run --rm -it \
  -v $(pwd)/data:/app/data \
  -p 8080:8080 \
  -e FITBIT_CLIENT_ID="your-client-id" \
  -e FITBIT_CLIENT_SECRET="your-client-secret" \
  ghcr.io/origox/syncbit:latest --authorize

# Upload the tokens to the Kubernetes PVC
kubectl cp ./data/fitbit_tokens.json <syncbit-pod>:/app/data/

# Or create a configmap and mount it
kubectl create configmap syncbit-tokens --from-file=./data/fitbit_tokens.json
```

The authorization tokens will be stored in the persistent volume.

### Method 3: ArgoCD Deployments

When using ArgoCD, use parameter overrides to avoid sync conflicts:

```bash
# Step 1: Scale down deployment via ArgoCD parameter
argocd app set syncbit -p replicaCount=0

# Step 2: Enable authorization job via ArgoCD parameter
argocd app set syncbit -p authorization.createJob=true

# Step 3: Get the job pod and view logs
kubectl get pods -l app.kubernetes.io/component=authorization
POD_NAME=$(kubectl get pods -l app.kubernetes.io/component=authorization -o jsonpath='{.items[0].metadata.name}')
kubectl logs -f $POD_NAME

# Step 4: Port-forward and complete OAuth (open URL from logs in browser)
kubectl port-forward $POD_NAME 8080:8080

# Step 5: Clean up via ArgoCD parameters
argocd app set syncbit -p authorization.createJob=false
argocd app set syncbit -p replicaCount=1
```

**Justfile Automation:**

Create a `justfile` with this command for easier authorization:

```justfile
# Interactive OAuth authorization for ArgoCD deployments
authorize-argocd app_name="syncbit":
    #!/usr/bin/env bash
    set -euo pipefail

    echo "Starting authorization for {{ app_name }}..."
    argocd app set {{ app_name }} -p replicaCount=0 -p authorization.createJob=true

    echo "Waiting for job pod to start..."
    sleep 5
    POD_NAME=$(kubectl get pods -l app.kubernetes.io/component=authorization -o jsonpath='{.items[0].metadata.name}')

    echo "=== Authorization URL ==="
    kubectl logs $POD_NAME | grep "https://www.fitbit.com"

    echo -e "\nStarting port-forward. Open the URL above in your browser."
    kubectl port-forward $POD_NAME 8080:8080 &
    PF_PID=$!

    read -p "Press Enter after completing authorization in browser..."

    kill $PF_PID 2>/dev/null || true
    argocd app set {{ app_name }} -p replicaCount=1 -p authorization.createJob=false

    echo "Done! Deployment should be running now."
```

Usage: `just authorize-argocd`

**Why ArgoCD requires parameter overrides:**

- ArgoCD continuously syncs from Git to enforce desired state
- Using `kubectl scale` directly would be reverted by ArgoCD
- Parameter overrides (`argocd app set -p`) tell ArgoCD about intentional drift
- This keeps tokens out of Git while maintaining GitOps principles

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
| `externalSecrets.secretStore.name` | SecretStore name | `onepassword` |
| `externalSecrets.secretStore.kind` | SecretStore kind | `ClusterSecretStore` |

**1Password Item Structure:**

When using External Secrets Operator, the chart expects the following 1Password items in the `kubernetes` vault:

- **Item: `syncbit`**
  - Field: `FITBIT_CLIENT_ID`
  - Field: `FITBIT_CLIENT_SECRET`

- **Item: `victoria-metrics`**
  - Field: `USER`
  - Field: `PASSWORD`
  - Field: `ENDPOINT`

The secret paths are hardcoded in the ExternalSecret template. To use different item names or fields, modify `templates/externalsecret.yaml`.

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

### Authorization

| Parameter | Description | Default |
|-----------|-------------|---------|
| `authorization.createJob` | Create Kubernetes Job for OAuth authorization | `false` |

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
