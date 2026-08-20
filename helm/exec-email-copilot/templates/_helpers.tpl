{{- define "exec-email-copilot.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "exec-email-copilot.fullname" -}}
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

{{- define "exec-email-copilot.labels" -}}
helm.sh/chart: {{ include "exec-email-copilot.name" . }}-{{ .Chart.Version | replace "+" "_" }}
{{ include "exec-email-copilot.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "exec-email-copilot.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "exec-email-copilot.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- "default" }}
{{- end }}
{{- end }}

{{- define "exec-email-copilot.selectorLabels" -}}
app.kubernetes.io/name: {{ include "exec-email-copilot.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
