# Run as Administrator after every WSL restart (or on boot via Task Scheduler).
#
# WSL runs in NAT mode — mirrored networking is OFF on purpose: the kernel's
# NFS client cannot open connections in mirrored mode (userspace sockets work,
# mount(2) times out), which held every download at 0% for a morning. NAT fixes
# NFS but gives WSL a fresh IP each boot, so the port proxies that let the
# laptop reach qBittorrent/worker/Prowlarr must be re-pointed here.
#
# The iphlpsvc restart is not decoration: portproxy rules added while the
# service runs may never bind their listener (observed: config present,
# netstat empty), and only a restart materialises them.

$wslIp = (wsl hostname -I).Trim().Split(" ")[0]
if (-not $wslIp) { Write-Error "WSL is not running"; exit 1 }

foreach ($port in 8010, 8080, 9696) {
  netsh interface portproxy set v4tov4 listenport=$port listenaddress=0.0.0.0 connectport=$port connectaddress=$wslIp
}
Restart-Service iphlpsvc -Force
netsh interface portproxy show v4tov4
Write-Host "proxies -> $wslIp"
