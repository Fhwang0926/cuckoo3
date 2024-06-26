#!/bin/bash
# /etc/init.d/cuckoo
# chmod 755 /etc/init.d/cuckoo.service

start() {
  stop
  # nohup bash -c /opt/cuckoo3.1/utils/cuckoo.service
  bash -c "nohup sudo -u cuckoo /opt/cuckoo3.1/utils/cuckoo.service > /var/log/cuckoo/cuckoo.log 2>&1 & echo \$! > /tmp/cuckoo.pid"
  # echo $! > /tmp/cuckoo.pid && cat /tmp/cuckoo.pid
  # /opt/cuckoo3.1/venv/bin/cuckoorooter /tmp/cuckoo3-rooter.sock --cwd /home/cuckoo/.cuckoocwd --iptables /sbin/iptables --ip /sbin/ip --openvpn /usr/sbin/openvpn --group cuckoo --debug
  cat /tmp/cuckoo.pid
}

stop() {
  cd /opt/cuckoo3.1
  # PID 파일 존재 여부 및 프로세스 실행 상태 확인
  if [ -f /tmp/cuckoo.pid ]; then
    if ps -p $(cat /tmp/cuckoo.pid) > /dev/null; then
      echo "Cuckoo is already running with pid : $(cat /tmp/cuckoo.pid)"
      echo "stopping cuckoo"
      /usr/bin/kill -9 $(cat /tmp/cuckoo.pid)
      echo "stopped..."
      sleep 5
      rm -rf /home/cuckoo/.cuckoocwd/operational/sockets/*.socket
      rm -rf /home/cuckoo/.cuckoocwd/operational/sockets/*.sock
      echo "removed socket"
    else
      # 프로세스 ID가 존재하지만 프로세스가 실행 중이지 않은 경우
      echo "PID file exists but Cuckoo is not running. Cleaning up..."
      rm /tmp/cuckoo.pid
      rm -rf /home/cuckoo/.cuckoocwd/operational/sockets/*.socket
      rm -rf /home/cuckoo/.cuckoocwd/operational/sockets/*.sock
      echo "removed socket"
    fi
  fi
  pkill -f '/opt/cuckoo3/venv/bin/python3'
}

status() {
  if ps -p $(cat /tmp/cuckoo.pid) > /dev/null
  then
    echo -e "\033[0;32mcuckoo is running"
  else
    echo -e "\033[0;31mcuckoo is not running"
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