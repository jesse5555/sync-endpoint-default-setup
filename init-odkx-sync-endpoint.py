#! /usr/bin/env python3

"""An interactive script to configure ODK-X sync endpoint on first run.

This is a first attempt at a proof of concept script, and has no
support for internationalization.

"""
import time
import os
import re
import subprocess
import typer
import json
import locale
import gettext
from tempfile import mkstemp
from shutil import move, copymode
from os import fdopen, remove, path
from typing import Dict, Union, Any
from xml import dom

app = typer.Typer(add_completion=False)

_: Any

def setup_locale() -> str:
# setup locale
    defLocale = locale.getdefaultlocale()
    languageCounty = defLocale[0]   # en_UK
    if not languageCounty:
        # if language is not one of the supported languages, we use english
        typer.echo("Oups! No translations found for your language... Using english.")
        typer.echo("Please send your translations for improvements.")
        typer.echo("")
        language = 'en'
    else:
        language = languageCounty[:2]  # en
    return language

def setup_translator(language_code):
    localedir = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'locale')
    translate = gettext.translation('odk-x-sync-endpoint-init', localedir, languages=[language_code], fallback=True)
    translate.install()
    _ = translate.gettext

def run_interactive_config():
    env_file_location = os.path.join(os.path.dirname(__file__), "config", "https.env")

    try:
        env = parse_env_file(env_file_location)
        typer.echo(_("Found configuration at {env_file_location}").format(env_file_location=env_file_location))
    except OSError:
        typer.echo(_("No default https configuration file found at expected path {env_file_location}. This prevents automatically renewing certs!").format(env_file_location=env_file_location))
        typer.echo(_("Please check your paths and file permissions, and make sure your config repo is up to date."))
        raise typer.Exit()

    typer.echo(_("Welcome to the ODK-X sync endpoint installation!"))
    typer.echo(_("This script will guide you through setting up your installation"))
    typer.echo(_("We'll need some information from you to get started though..."))
    time.sleep(1)
    typer.echo("")

    if is_cache_present() and is_complete_cache():
        allow_cache = typer.confirm(_("Do you wish to use cached configuratilon?"), default=True)
        typer.echo("")
        if allow_cache:
            cached_data = load_progress()
            enforce_https = cached_data['enforce_https']
            manual_certificate = cached_data['manual_certificate']
            if manual_certificate :
                setup_manual_certificate(env)
            else:
                setup_certbot_certificate(env)
        else:
            os.remove('progress.json')
            enforce_https = run_cache_setup(env)
    else:
        enforce_https = run_cache_setup(env)

    write_to_env_file(env_file_location, env)

    return (enforce_https, env)

def run_cache_setup(envParam: Dict[str, str]) -> bool:

    typer.echo(_("Please input the domain name you will use for this installation. A valid domain name is required for HTTPS without distributing custom certificates."))
    input_domain: str = typer.prompt(_("domain [({envParam_https_domain})]").format(envParam_https_domain=envParam['HTTPS_DOMAIN']), default=envParam['HTTPS_DOMAIN'], show_default=False)

    check_valid_domain(input_domain)
    env['HTTPS_DOMAIN'] = input_domain
    save_progress('env', {'HTTPS_DOMAIN': input_domain})
    typer.echo("")

    use_custom_password = typer.confirm(_("Do you want to use a custom LDAP administration password?"))

    if use_custom_password:
        typer.echo("")
        typer.echo(_("Please input the password to use for ldap admin"))
        default_ldap_pwd: str = typer.prompt(_("Ldap admin password"), hide_input=True)

        if default_ldap_pwd != "":
            replaceInFile("ldap.env", r"^\s*LDAP_ADMIN_PASSWORD=.*$", "LDAP_ADMIN_PASSWORD={}".format(default_ldap_pwd))
            typer.echo(_("Password set to: {default_ldap_pwd}").format(default_ldap_pwd=default_ldap_pwd))

    typer.echo(_("Would you like to enforce HTTPS? We recommend yes."))
    enforce_https = is_enforce_https()


    if not enforce_https:
        for i in range(1):
            typer.echo(_("Would you like to run an INSECURE and DANGEROUS server that will share your users's information if exposed to the Internet?"))
            insecure = typer.confirm(_("run insecure?"))
            if insecure:
                break
            if i==0:
                raise RuntimeError(_("HTTPS is currently required to run a secure public server. Please restart and select to enforce HTTPS"))

    save_progress('enforce_https', enforce_https)

    typer.echo(_("Enforcing https: {enforce_https}").format(enforce_https=enforce_https))
    if enforce_https:
        typer.echo(_("Please provide an admin email for security updates with HTTPS registration"))
        input_email: str = typer.prompt(_("admin email [({envParam_https_admin_email})]").format(envParam_https_admin_email=envParam['HTTPS_ADMIN_EMAIL']), default=envParam['HTTPS_ADMIN_EMAIL'], show_default=False)

        check_valid_email(input_email)
        env["HTTPS_ADMIN_EMAIL"] = input_email
        save_progress('env', {'HTTPS_DOMAIN': input_domain, 'HTTPS_ADMIN_EMAIL': input_email})

        typer.echo(_("The system will now attempt to setup an HTTPS certificate for this server."))
        typer.echo(_("For this to work you must have already have purchased/acquired a domain name (or subdomain) and setup a DNS A or AAAA record to point at this server's IP address."))
        typer.echo(_("If you have not done this yet, please do it now..."))
        time.sleep(1)
        proceed = typer.confirm(_("Domain is ready to proceed with certificate acquisition?"), default=True)
        if not proceed:
            typer.echo(_("Re-run this script once the domain is ready!"))
            raise typer.Exit()

        manual_certificate: str = typer.prompt(_("Do you wish to supply your own SSL certificate? If not, the script will use certbot (please make sure it is already installed)."), default=False)

        if not manual_certificate:
            setup_certbot_certificate(envParam)
        else:
            setup_manual_certificate(envParam)
        
        save_progress('manual_certificate', manual_certificate)
        typer.echo(_("Attempting to save updated https configuration"))
    return enforce_https

def is_enforce_https() -> bool:
    return typer.confirm(_("enforce https?"), default=True)

def check_valid_email(email):
    pattern = r"^[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*@(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$"

    if not (email!="" and re.match(pattern, email)):
        typer.echo(_("Invalid email address: {email}").format(email=email))
        typer.echo(_("Re-run this script with the correct email address."))
        raise typer.Exit()

def check_valid_domain(domain):
    pattern = r"^([A-Za-z0-9]\.|[A-Za-z0-9][A-Za-z0-9-]{0,61}[A-Za-z0-9]\.){1,3}[A-Za-z]{2,6}$"
    if not (domain!="" and re.match(pattern, domain)):
        typer.echo(_("Invalid domain: {domain}").format(domain=domain))
        typer.echo(_("Re-run this script with the correct domain."))
        raise typer.Exit()

def is_cache_present() -> bool:
    return os.path.exists('progress.json') and os.stat('progress.json').st_size != 0 

def save_progress(key: str, value: Union[str, bool, Dict[str, str]]):
    if is_cache_present():
        with open('progress.json', 'r') as progress_file:
            data: Dict[str,  Union[str, bool, Dict[str, str]]] = json.load(progress_file) 
            data.update({key: value})
    else :
        data = {key: value}

    with open('progress.json', 'w') as progress_file:
        json.dump(data, progress_file)

def load_progress() -> Dict[str,  Union[str, bool, Dict[str, str]]]:
    with open('progress.json', 'r') as progress_file:
        return json.load(progress_file)

def is_complete_cache() -> bool:
    data = load_progress()
    return not (('env' not in data.keys()) or ('enforce_https' not in data.keys()) or ('manual_certificate' not in data.keys()))


def replaceInFile(file_path: str , pattern: str, subst: str):
    fh, abs_path = mkstemp()
    with fdopen(fh,'w') as new_file:
        with open(file_path) as old_file:
            for line in old_file:
                new_file.write(re.sub(pattern, subst, line))
    copymode(file_path, abs_path)
    remove(file_path)
    move(abs_path, file_path)

def setup_manual_certificate(env: Dict[str, str]):
    cert_fullchain_path: str = env['CERT_FULLCHAIN_PATH']
    cert_privkey_path: str = env['CERT_PRIVKEY_PATH']
    typer.echo(_('Please enter path to fullchain .pem/.crt file'))
    cert_fullchain_path = typer.prompt(_("fullchain file [({cert_fullchain_path})]").format(cert_fullchain_path=cert_fullchain_path), default=cert_fullchain_path, show_default=False)
    typer.echo(_('Please enter path to private key .pem file'))
    cert_privkey_path = typer.prompt(_("private key file [({cert_privkey_path})]").format(cert_fullchain_path=cert_fullchain_path), default=cert_privkey_path, show_default=False)
    if not cert_fullchain_path or not cert_privkey_path:
        typer.echo(_('Input not provided, re-run this script with correct inputs.'))
        raise typer.Exit()
    # Compute absolute paths from relative path inputs
    cert_fullchain_path = path.abspath(cert_fullchain_path)
    cert_privkey_path = path.abspath(cert_privkey_path)
    if not path.exists(cert_fullchain_path) or not path.exists(cert_privkey_path):
        typer.echo(_('File at the given path do not exists, re-run this script with correct inputs.'))
        raise typer.Exit()
    env['CERT_FULLCHAIN_PATH'] = cert_fullchain_path
    env['CERT_PRIVKEY_PATH'] = cert_privkey_path

def setup_certbot_certificate(env: Dict[str, str]):
    typer.echo(_("Please enter your system Password"))
    try:
        subprocess.run("sudo certbot certonly --standalone \
        --email {} \
        -d {} \
        --rsa-key-size 4096 \
        --agree-tos \
        --cert-name bootstrap \
        --keep-until-expiring \
        --non-interactive".format(env["HTTPS_ADMIN_EMAIL"], env["HTTPS_DOMAIN"]), shell=True, check=True)
    except subprocess.CalledProcessError:
        typer.echo(_("Error setting up certbot certificate."))
        typer.echo("")

def write_to_env_file(filepath: str, env: Dict[str, str]):
    """A janky in-memory file write.

    This is not atomic and would use lots of ram for large files.
    """
    with open(filepath, mode="w") as f:
        for (key, val) in env.items():
            f.write("{}={}\n".format(key, val))


def parse_env_file(filepath: str) -> Dict[str, str]:
    env = {}
    with open(filepath) as f:
        for line in f:
            try:
                key, val = line.split('=')
            except Exception:
                continue
            env[key] = val.strip()
    return env


def run_docker_builds():
    try:
        subprocess.run("docker build --pull -t odk/sync-web-ui https://github.com/odk-x/sync-endpoint-web-ui.git", shell=True, check=True)
        subprocess.run("docker build --pull -t odk/db-bootstrap db-bootstrap", shell=True, check=True)
        subprocess.run("docker build --pull -t odk/openldap openldap", shell=True, check=True)
        subprocess.run("docker build --pull -t odk/phpldapadmin phpldapadmin", shell=True, check=True)
    except subprocess.CalledProcessError:
        typer.echo(_("Error pulling required docker images."))
        typer.echo("")

def run_sync_endpoint_build():
    try:
        subprocess.run("git clone -b master --single-branch --depth=1 https://github.com/odk-x/sync-endpoint ; \
                cd sync-endpoint ; \
                mvn -pl org.opendatakit:sync-endpoint-war,org.opendatakit:sync-endpoint-docker-swarm,org.opendatakit:sync-endpoint-common-dependencies clean install -DskipTests", shell=True, check=True)
    except subprocess.CalledProcessError:
        typer.echo(_("Error building sync endpoint."))
        typer.echo("")

def deploy_stack(use_https: bool, env: Dict[str, str]):
    try:
        if use_https:
            is_certbot = 'CERT_FULLCHAIN_PATH' not in env
            config = 'docker-compose-https-certbot.yml' if is_certbot else 'docker-compose-https.yml'
            envstring = ""
            if not is_certbot:
                envstring = "env CERT_FULLCHAIN_PATH={} CERT_PRIVKEY_PATH={}".format(env["CERT_FULLCHAIN_PATH"], env["CERT_PRIVKEY_PATH"])
            subprocess.run("{} docker stack deploy -c docker-compose.yml -c {} syncldap".format(envstring, config), shell=True, check=True)
        else:
            subprocess.run("docker stack deploy -c docker-compose.yml syncldap", shell=True, check=True)
    except subprocess.CalledProcessError:
        typer.echo(_("Error deploying stack."))
        typer.echo("")

@app.callback(invoke_without_command=True)
def install():
    language_code = setup_locale() 
    setup_translator(language_code)
    https, env = run_interactive_config()
    run_docker_builds()
    run_sync_endpoint_build()
    deploy_stack(https, env)

if __name__ == "__main__":
    app()

