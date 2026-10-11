// Fetch the last version of k3s
data "http" "k3s_version" {
  count = var.k3s_version == "latest" ? 1 : 0

  url = "https://update.k3s.io/v1-release/channels"
}

// Fetch the k3s installation script
data "http" "k3s_installer" {
  url = "https://raw.githubusercontent.com/rancher/k3s/${local.k3s_version}/install.sh"
}

locals {
  // Use the fetched version if 'latest' is specified
  k3s_version = var.k3s_version == "latest" ? jsondecode(data.http.k3s_version[0].response_body).data[1].latest : var.k3s_version
}
