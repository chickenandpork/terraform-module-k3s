#!/usr/bin/env python3

import base64
import json
import os
import shlex
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional, Tuple


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    sys.exit(1)


def read_query() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        fail(f"invalid terraform external query: {exc}")


def decode_connection(query: dict) -> dict:
    encoded = query.get("connection_json_b64")
    if not encoded:
        fail("missing connection_json_b64")
    try:
        return json.loads(base64.b64decode(encoded).decode("utf-8"))
    except Exception as exc:
        fail(f"unable to decode connection_json_b64: {exc}")


def timeout_seconds(value) -> Optional[int]:
    if value in (None, "", 0):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return max(1, int(value))
    text = str(value).strip()
    try:
        return int(text)
    except ValueError:
        pass
    if text.endswith("s"):
        return int(text[:-1])
    if text.endswith("m"):
        return int(text[:-1]) * 60
    if text.endswith("h"):
        return int(text[:-1]) * 3600
    return None


def write_temp(content: str, suffix: str = "") -> str:
    fd, path = tempfile.mkstemp(prefix="k3s-live-kubeconfig-", suffix=suffix)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)
    os.chmod(path, 0o600)
    return path


def build_ssh_base_args(connection: dict, helper_path: str, temp_paths: List[str]) -> Tuple[List[str], Dict[str, str]]:
    if connection.get("type", "ssh") != "ssh":
        fail(f"unsupported connection type: {connection.get('type')}")

    host = connection.get("host")
    if not host:
        fail("missing host")

    args = ["ssh", "-o", "LogLevel=ERROR"]
    env = os.environ.copy()

    if connection.get("user"):
        args += ["-l", str(connection["user"])]

    if connection.get("port") not in (None, ""):
        args += ["-p", str(connection["port"])]

    if connection.get("timeout") not in (None, ""):
        seconds = timeout_seconds(connection["timeout"])
        if seconds:
            args += ["-o", f"ConnectTimeout={seconds}"]

    if connection.get("host_key"):
        known_hosts_path = write_temp(str(connection["host_key"]).strip() + "\n")
        temp_paths.append(known_hosts_path)
        args += [
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={known_hosts_path}",
        ]
    else:
        args += ["-o", "StrictHostKeyChecking=accept-new"]

    if connection.get("private_key"):
        private_key_path = write_temp(str(connection["private_key"]))
        temp_paths.append(private_key_path)
        args += ["-i", private_key_path, "-o", "IdentitiesOnly=yes"]

    if connection.get("certificate"):
        certificate_path = write_temp(str(connection["certificate"]))
        temp_paths.append(certificate_path)
        args += ["-o", f"CertificateFile={certificate_path}"]

    if connection.get("password"):
        env["SSH_ASKPASS"] = helper_path
        env["SSH_ASKPASS_REQUIRE"] = "force"
        env["DISPLAY"] = env.get("DISPLAY", "none")
        env["TF_SSH_ASKPASS_PASSWORD"] = str(connection["password"])

    if connection.get("agent"):
        args += ["-o", "BatchMode=yes"]

    return args + [host], env


def proxy_command(connection: dict, helper_path: str, temp_paths: List[str]) -> str:
    bastion = {
        "type": connection.get("type", "ssh"),
        "host": connection.get("bastion_host"),
        "user": connection.get("bastion_user"),
        "password": connection.get("bastion_password"),
        "port": connection.get("bastion_port"),
        "timeout": connection.get("timeout"),
        "private_key": connection.get("bastion_private_key"),
        "certificate": connection.get("bastion_certificate"),
        "host_key": connection.get("bastion_host_key"),
        "agent": connection.get("agent"),
    }

    bastion_args, _ = build_ssh_base_args(bastion, helper_path, temp_paths)
    bastion_host = bastion_args.pop()
    bastion_args += ["-W", "%h:%p", bastion_host]

    prefix = []
    if bastion.get("password"):
        prefix = [
            f"TF_SSH_ASKPASS_PASSWORD={shlex.quote(str(bastion['password']))}",
            f"SSH_ASKPASS={shlex.quote(helper_path)}",
            "SSH_ASKPASS_REQUIRE=force",
            "DISPLAY=none",
        ]

    command = shlex.join(bastion_args)
    if prefix:
        command = " ".join(prefix + [command])
    return command


def fetch_kubeconfig(connection: dict, kubeconfig_path: str) -> bytes:
    temp_paths: List[str] = []
    helper_path = write_temp(
        "#!/bin/sh\nexec printf '%s\\n' \"$TF_SSH_ASKPASS_PASSWORD\"\n",
        suffix=".sh",
    )
    temp_paths.append(helper_path)
    os.chmod(helper_path, 0o700)

    try:
        ssh_args, env = build_ssh_base_args(connection, helper_path, temp_paths)

        if connection.get("bastion_host"):
            ssh_args_host = ssh_args.pop()
            ssh_args += ["-o", f"ProxyCommand={proxy_command(connection, helper_path, temp_paths)}", ssh_args_host]

        quoted_path = shlex.quote(kubeconfig_path)
        remote_command = f"sh -lc {shlex.quote(f'sudo -n cat {quoted_path} 2>/dev/null || cat {quoted_path}')}"
        ssh_args.append(remote_command)

        proc = subprocess.run(
            ssh_args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
        )

        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", errors="replace").strip()
            fail(f"ssh failed with exit code {proc.returncode}: {stderr}")

        return proc.stdout
    finally:
        for path in temp_paths:
            try:
                os.remove(path)
            except OSError:
                pass


def main() -> None:
    query = read_query()
    connection = decode_connection(query)
    kubeconfig_path = query.get("kubeconfig_path", "/etc/rancher/k3s/k3s.yaml")

    kubeconfig = fetch_kubeconfig(connection, kubeconfig_path)
    print(json.dumps({"kubeconfig_b64": base64.b64encode(kubeconfig).decode("ascii")}))


if __name__ == "__main__":
    main()
