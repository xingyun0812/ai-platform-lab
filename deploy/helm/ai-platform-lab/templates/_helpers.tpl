{{/*
Expand the name of the chart.
*/}}
{{- define "ai-platform-lab.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "ai-platform-lab.fullname" -}}
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
Create chart label.
*/}}
{{- define "ai-platform-lab.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels.
*/}}
{{- define "ai-platform-lab.labels" -}}
helm.sh/chart: {{ include "ai-platform-lab.chart" . }}
{{ include "ai-platform-lab.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- with .Values.global.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end }}

{{/*
Selector labels.
*/}}
{{- define "ai-platform-lab.selectorLabels" -}}
app.kubernetes.io/name: {{ include "ai-platform-lab.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Gateway image with optional registry prefix.
*/}}
{{- define "ai-platform-lab.gateway-image" -}}
{{- $registry := .Values.global.imageRegistry -}}
{{- $repo := .Values.gateway.image.repository -}}
{{- $tag := .Values.gateway.image.tag | default "latest" -}}
{{- if $registry -}}
{{- printf "%s/%s:%s" $registry $repo $tag -}}
{{- else -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}
{{- end }}

{{/*
Worker image with optional registry prefix.
*/}}
{{- define "ai-platform-lab.worker-image" -}}
{{- $registry := .Values.global.imageRegistry -}}
{{- $repo := .Values.worker.image.repository -}}
{{- $tag := .Values.worker.image.tag | default "latest" -}}
{{- if $registry -}}
{{- printf "%s/%s:%s" $registry $repo $tag -}}
{{- else -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}
{{- end }}

{{/*
Gateway service account name.
*/}}
{{- define "ai-platform-lab.gateway-serviceAccountName" -}}
{{- if .Values.gateway.serviceAccount.create }}
{{- default (printf "%s-gateway" (include "ai-platform-lab.fullname" .)) .Values.gateway.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.gateway.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Worker service account name.
*/}}
{{- define "ai-platform-lab.worker-serviceAccountName" -}}
{{- if .Values.worker.serviceAccount.create }}
{{- default (printf "%s-worker" (include "ai-platform-lab.fullname" .)) .Values.worker.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.worker.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Image pull secrets.
*/}}
{{- define "ai-platform-lab.imagePullSecrets" -}}
{{- if .Values.global.imagePullSecrets }}
imagePullSecrets:
{{- range .Values.global.imagePullSecrets }}
  - name: {{ . }}
{{- end }}
{{- end }}
{{- end }}
