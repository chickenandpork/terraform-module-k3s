data "external" "live_kube_config" {
  depends_on = [null_resource.kubernetes_ready]

  program = ["python3", "${path.module}/scripts/fetch_live_kubeconfig.py"]

  query = {
    connection_json_b64 = base64encode(jsonencode(local.root_server_connection))
    depends_on_id       = null_resource.kubernetes_ready.id
    kubeconfig_path     = "/etc/rancher/k3s/k3s.yaml"
  }
}
