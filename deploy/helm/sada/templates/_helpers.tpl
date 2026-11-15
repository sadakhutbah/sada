{{/*
Chart name and common labels.
*/}}
{{- define "sada.name" -}}
sada
{{- end }}

{{- define "sada.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else if eq .Release.Name "sada" -}}
{{- include "sada.name" . -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "sada.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end }}

{{- define "sada.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end }}

{{- define "sada.labels" -}}
helm.sh/chart: {{ include "sada.chart" . }}
app.kubernetes.io/name: {{ include "sada.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: sada
{{- end }}

{{- define "sada.selectorLabels" -}}
app.kubernetes.io/name: {{ include "sada.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "sada.image" -}}
{{- printf "%s:%s" .Values.image.repository (.Values.image.tag | default .Chart.AppVersion) -}}
{{- end }}

{{/* Name of the shared PVC (/app/data: recordings, state, uploads). */}}
{{- define "sada.pvcName" -}}
{{- if .Values.persistence.existingClaim -}}
{{- .Values.persistence.existingClaim -}}
{{- else -}}
{{- printf "%s-data" (include "sada.fullname" .) -}}
{{- end -}}
{{- end }}

{{/* Name of the CloudNativePG Cluster. */}}
{{- define "sada.postgresName" -}}
{{- .Values.database.cnpg.name | default (printf "%s-postgres" (include "sada.fullname" .)) -}}
{{- end }}

{{/* Secure cookies: explicit value, or automatic when the Ingress terminates TLS. */}}
{{- define "sada.secureCookies" -}}
{{- $v := .Values.backoffice.secureCookies | toString -}}
{{- if eq $v "" -}}
{{- if and .Values.ingress.enabled .Values.ingress.tls.enabled -}}true{{- else -}}false{{- end -}}
{{- else -}}
{{- $v -}}
{{- end -}}
{{- end }}

{{/*
SADA_DATABASE_URL, from the CloudNativePG secret (key `uri`), an existing secret, or a literal URL.
Empty when the mode is sqlite: the app then keeps its SQLite file under /app/data.
*/}}
{{- define "sada.databaseEnv" -}}
{{- if eq .Values.database.mode "cnpg" }}
- name: SADA_DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "sada.postgresName" . }}-app
      key: uri
{{- else if eq .Values.database.mode "external" }}
{{- if .Values.database.existingSecret }}
- name: SADA_DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ .Values.database.existingSecret }}
      key: {{ .Values.database.existingSecretKey | default "uri" }}
{{- else if .Values.database.url }}
- name: SADA_DATABASE_URL
  value: {{ .Values.database.url | quote }}
{{- end }}
{{- end }}
{{- end }}

{{/* Volumes shared by the back office and the orchestrators. */}}
{{- define "sada.dataVolume" -}}
- name: data
{{- if .Values.persistence.enabled }}
  persistentVolumeClaim:
    claimName: {{ include "sada.pvcName" . }}
{{- else }}
  emptyDir: {}
{{- end }}
{{- end }}
