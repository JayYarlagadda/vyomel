{{- define "vyomel.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "vyomel.fullname" -}}
{{- default .Chart.Name .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "vyomel.labels" -}}
app.kubernetes.io/name: {{ include "vyomel.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- define "vyomel.selectorLabels" -}}
app.kubernetes.io/name: {{ include "vyomel.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "vyomel.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "vyomel.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "vyomel.postgresHost" -}}
{{- printf "%s-postgres" (include "vyomel.fullname" .) -}}
{{- end -}}

{{- define "vyomel.redisHost" -}}
{{- printf "%s-redis" (include "vyomel.fullname" .) -}}
{{- end -}}

{{- define "vyomel.databaseUrl" -}}
postgresql+asyncpg://{{ .Values.postgres.user }}:$(VYOMEL_POSTGRES_PASSWORD)@{{ include "vyomel.postgresHost" . }}:5432/{{ .Values.postgres.database }}
{{- end -}}
