# 驗證環境映像：把設定檔檢查從「讀字串斷言」升級為「用目標程式實際載入」。
#
# 以 Envoy 官方映像為底，再補上其餘驗證所需的真二進位：
#   envoy       envoy --mode validate      —— Envoy bootstrap 實際載入
#   nginx       nginx -t                   —— 影子閘道設定實際載入
#   kubeconform K8s API schema 嚴格驗證     —— 不需叢集
#   kubectl     有叢集時可做 --dry-run=server
#   python3     跑 diff/ 與 verify/ 本身（PyYAML 由 python3-yaml 提供）
#
# 建置與執行方式見 README「在 wslc 容器中執行驗證」。
FROM envoyproxy/envoy:v1.31-latest

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-yaml bash curl openssl ca-certificates nginx \
 && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL -o /usr/local/bin/kubectl \
      "https://dl.k8s.io/release/v1.31.0/bin/linux/amd64/kubectl" \
 && chmod 0755 /usr/local/bin/kubectl

RUN curl -fsSL "https://github.com/yannh/kubeconform/releases/download/v0.6.7/kubeconform-linux-amd64.tar.gz" \
      | tar -xz -C /usr/local/bin kubeconform \
 && chmod 0755 /usr/local/bin/kubeconform

# nginx-shadow.conf 引用的內部 CA。只需是合法 PEM，內容不影響設定驗證；
# 缺這個檔案 nginx -t 會在 proxy_ssl_trusted_certificate 直接 emerg。
RUN mkdir -p /etc/nginx/ca \
 && cp /etc/ssl/certs/ca-certificates.crt /etc/nginx/ca/internal-ca.pem

# 底層映像的 entrypoint 是 envoy，清掉才能直接下 python3 / bash 等命令
ENTRYPOINT []
WORKDIR /
