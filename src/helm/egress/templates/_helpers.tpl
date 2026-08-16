{{- define "mastrao-recording-egress.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "mastrao-recording-egress.fullname" -}}
{{- printf "%s-%s" .Release.Name (include "mastrao-recording-egress.name" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "mastrao-recording-egress.labels" -}}
app.kubernetes.io/name: {{ include "mastrao-recording-egress.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end }}

{{- define "mastrao-recording-egress.selectorLabels" -}}
app.kubernetes.io/name: {{ include "mastrao-recording-egress.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
