# Sistema analítico TPM–SMED | World Plast S.R.L.

Aplicación web Streamlit para presentar el análisis histórico de disponibilidad, Pareto de pérdidas y simulación de escenarios TPM–SMED, con conexión de solo lectura a Google Sheets.

## Archivos
- `app.py`: aplicación web.
- `requirements.txt`: dependencias.
- `.gitignore`: evita subir secretos locales.

## Datos históricos de referencia
Periodo: julio de 2024 a junio de 2025.
- Tiempo programado: 181.200,00 min.
- Tiempo improductivo: 24.475,71 min.
- Disponibilidad global recalculada: 86,4924 %.
- Mantenimiento: 128 eventos y 10.423,34 min.
- Setup: 92 eventos y 9.645,16 min.

Los valores históricos anteriores son agregados validados en el notebook y se muestran por separado de los registros actuales de Google Sheets. Los escenarios TO-BE son simulaciones, no resultados observados.

## Configurar conexión segura a Google Sheets

La aplicación usa una cuenta de servicio de Google y permisos de solo lectura. No pegues credenciales en `app.py`, en este README ni en el repositorio.

1. En Google Cloud Console, crea o selecciona un proyecto.
2. Habilita **Google Sheets API** y **Google Drive API**.
3. Crea una cuenta de servicio y genera una clave JSON. Descárgala y guárdala de forma segura.
4. Abre el JSON y copia sus campos para configurar los secretos de Streamlit.
5. Comparte el Google Sheet con el valor `client_email` de la cuenta de servicio, como **Lector**. No lo hagas público.
6. En Streamlit Community Cloud, al desplegar, abre **Advanced settings → Secrets** y pega la configuración TOML. No subas el JSON al repositorio.

Ejemplo de estructura de secrets TOML (rellenar con los valores reales del JSON, sin cambiar los nombres de las claves):

```toml
google_sheet_id = "1stqXpgvFta1thfsk8vi6xQOiX-vyuN0O7Q8yo0X9jv4"
google_worksheet = "REGISTROS"

[gcp_service_account]
type = "service_account"
project_id = "REEMPLAZAR"
private_key_id = "REEMPLAZAR"
private_key = """-----BEGIN PRIVATE KEY-----
REEMPLAZAR_CON_LA_CLAVE_PRIVADA_REAL
-----END PRIVATE KEY-----\n"""
client_email = "REEMPLAZAR"
client_id = "REEMPLAZAR"
token_uri = "https://oauth2.googleapis.com/token"
auth_uri = "https://accounts.google.com/o/oauth2/auth"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "REEMPLAZAR"
universe_domain = "googleapis.com"
```

Notas:
- Copia todos los campos que estén presentes en el JSON de tu cuenta de servicio; no publiques el archivo JSON.
- `private_key` debe conservar sus saltos de línea. Streamlit Secrets admite cadenas multilínea entre comillas triples.
- La aplicación consulta la pestaña `REGISTROS` de la hoja identificada por el ID anterior. Comprueba que ese ID y el nombre de la pestaña correspondan a tu hoja actual.
- La app usa credenciales de solo lectura (`spreadsheets.readonly`) y no escribe en la hoja.

## Publicar en GitHub y Streamlit Community Cloud

1. Crea una cuenta en GitHub.
2. Crea un repositorio público con un nombre como `tpm-smed-worldplast`.
3. Sube `app.py`, `requirements.txt`, `README.md` y `.gitignore` a la raíz del repositorio. **No subas secretos ni archivos JSON de credenciales.**
4. Inicia sesión en Streamlit Community Cloud con GitHub.
5. Selecciona **Create app**, elige el repositorio, rama `main` y archivo principal `app.py`.
6. En **Advanced settings → Secrets**, pega la configuración TOML ya completada con los datos reales de la cuenta de servicio.
7. Pulsa **Deploy** y espera a que termine la instalación.
8. Abre la URL `*.streamlit.app` generada y prueba todas las secciones.
9. Verifica que la sección **Datos de Google Sheets** pueda leer la pestaña `REGISTROS`.
10. Para que tu docente la use sin iniciar sesión, comprueba que la aplicación esté configurada como pública.

Documentación oficial:
- https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy
- https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management
- https://docs.streamlit.io/deploy/streamlit-community-cloud/share-your-app

## Pruebas antes de entregar
- Resumen ejecutivo: AS-IS aproximadamente 86,49 %.
- Pérdidas: mantenimiento y setup visibles en el Pareto.
- Simulador: prueba TPM 20 % y SMED 30 %; la disponibilidad simulada debe aproximarse a 89,24 %.
- Google Sheets: se debe mostrar el estado de conexión y el número de filas de `REGISTROS`.
- Confirmar que el acceso sea de solo lectura y que los registros vivos no se sumen al histórico sin conciliación.
