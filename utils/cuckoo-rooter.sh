#!/bin/bash
# /etc/init.d/cuckoo-router
# chmod 755 /etc/init.d/cuckoo-rooter

start() {
  stop
  bash -c "nohup /opt/cuckoo3.1/venv/bin/cuckoorooter /tmp/cuckoo3-rooter.sock --cwd /home/cuckoo/.cuckoocwd --iptables /sbin/iptables --ip /sbin/ip --openvpn /usr/sbin/openvpn --group cuckoo --debug > /var/log/cuckoo/cuckoo-rooter.log 2>&1 & echo \$! > /tmp/cuckoo-rooter.pid"
  cat /tmp/cuckoo-rooter.pid
}

stop() {
  # PID 파일 존재 여부 및 프로세스 실행 상태 확인
  if [ -f /tmp/cuckoo-rooter.pid ]; then
    if ps -p $(cat /tmp/cuckoo-rooter.pid) > /dev/null; then
      echo "Cuckoo-rooter is already running with pid : $(cat /tmp/cuckoo-rooter.pid)"
      echo "stopping cuckoo-rooter"
      /usr/bin/kill -9 $(cat /tmp/cuckoo-rooter.pid)
      rm -rf /tmp/cuckoo3-rooter.sock
      echo "stopped..."
      sleep 5
      echo "removed socket"
    else
      # 프로세스 ID가 존재하지만 프로세스가 실행 중이지 않은 경우
      echo "PID file exists but Cuckoo-rooter is not running. Cleaning up..."
      rm /tmp/cuckoo-rooter.pid
      echo "removed socket"
      rm -rf /tmp/cuckoo3-rooter.sock
    fi
  fi
  pkill -f '/opt/cuckoo3.1/venv/bin/cuckoorooter'
}

status() {
  if ps -p $(cat /tmp/cuckoo-rooter.pid) > /dev/null
  then
    echo -e "\033[0;32mcuckoo-rooter is running"
  else
    echo -e "\033[0;31mcuckoo-rooter is not running"
  fi
  echo -e "\033[0m"
}

restart() {
  stop
  start
}

case "$1" in
  start)
    start
    ;;
  stop)
    stop
    ;;
  restart)
    restart
    ;;
  status)
    status
    ;;
  *)
    echo $"Usage: $0 {start|stop|restart|status}"
    exit 1
esac