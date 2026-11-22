{{/*
Expand the name of the chart.
*/}}
{{- define "syncbit.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "syncbit.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "syncbit.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "syncbit.labels" -}}
helm.sh/chart: {{ include "syncbit.chart" . }}
{{ include "syncbit.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "syncbit.selectorLabels" -}}
app.kubernetes.io/name: {{ include "syncbit.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "syncbit.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "syncbit.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Create the name of the secret to use
*/}}
{{- define "syncbit.secretName" -}}
{{- if .Values.externalSecrets.enabled }}
{{- printf "%s-external" (include "syncbit.fullname" .) }}
{{- else }}
{{- printf "%s-secrets" (include "syncbit.fullname" .) }}
{{- end }}
{{- end }}

{{/*
Get the image tag
*/}}
{{- define "syncbit.imageTag" -}}
{{- .Values.image.tag | default .Chart.AppVersion }}
{{- end }}

{{/*
Fail early on an unsupported provider instead of rendering a pod that crashes.
*/}}
{{- define "syncbit.provider" -}}
{{- $p := .Values.config.healthProvider | default "google" | lower }}
{{- if not (has $p (list "google" "fitbit")) }}
{{- fail (printf "config.healthProvider must be \"google\" or \"fitbit\", got %q" $p) }}
{{- end }}
{{- $p }}
{{- end }}

{{/*
Secret-backed env vars shared by the Deployment and the authorization Job.
Only the selected provider's OAuth credentials are referenced, so the other
provider's keys need not exist in the Secret / 1Password item.
*/}}
{{- define "syncbit.secretEnv" -}}
{{- $secret := include "syncbit.secretName" . }}
{{- if eq (include "syncbit.provider" .) "google" }}
- name: GOOGLE_CLIENT_ID
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: google-client-id
- name: GOOGLE_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: google-client-secret
{{- else }}
- name: FITBIT_CLIENT_ID
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: fitbit-client-id
- name: FITBIT_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: fitbit-client-secret
{{- end }}
- name: VICTORIA_USER
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: victoria-user
- name: VICTORIA_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: victoria-password
- name: VICTORIA_ENDPOINT
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: victoria-endpoint
{{- end }}

{{/*
Provider-related env vars from the ConfigMap, shared by Deployment and Job.
*/}}
{{- define "syncbit.providerEnv" -}}
- name: HEALTH_PROVIDER
  valueFrom:
    configMapKeyRef:
      name: {{ include "syncbit.fullname" . }}
      key: health-provider
- name: GOOGLE_REDIRECT_URI
  valueFrom:
    configMapKeyRef:
      name: {{ include "syncbit.fullname" . }}
      key: google-redirect-uri
- name: FITBIT_REDIRECT_URI
  valueFrom:
    configMapKeyRef:
      name: {{ include "syncbit.fullname" . }}
      key: fitbit-redirect-uri
- name: FITBIT_USER_ID
  valueFrom:
    configMapKeyRef:
      name: {{ include "syncbit.fullname" . }}
      key: fitbit-user-id
{{- end }}
