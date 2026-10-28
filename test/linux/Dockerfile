# A Linux box like the ones Pilcrow is installed on, for checking the installer and the Qt window
# without a Linux machine at hand: Ubuntu 24.04, Node 20, the system Python (externally managed,
# so the installer's venv path is exercised), an X server in memory (Xvfb) and a CJK face.
#
#   docker build -t pilcrow-linux test/linux
#   docker run --rm -v "$PWD/out:/in:ro" -v "$PWD:/repo:ro" -v "$PWD/test/out:/out" pilcrow-linux
#
# Entry: test/linux/run.sh (installs from /in/pilcrow-install.sh, starts the mock server, opens the
# Qt window on Xvfb, drives every page, writes screenshots and logs to /out).
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl gnupg git python3 python3-venv python3-pip xvfb x11-utils \
      fonts-noto-cjk fonts-dejavu-core fontconfig \
      libgl1 libegl1 libglib2.0-0 libxkbcommon0 libxkbcommon-x11-0 libdbus-1-3 libfontconfig1 \
      libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 \
      libxcb-shape0 libxcb-xinerama0 libxcb-xfixes0 libxcb-xkb1 libxrender1 libxi6 libsm6 libice6 \
      libnss3 libasound2t64 libgbm1 \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*
RUN useradd -m -s /bin/bash tester
USER tester
WORKDIR /home/tester
# The scripts live outside /home/tester: that folder is a volume in the quick mode, and a volume
# keeps whatever was copied into it on its first use.
COPY --chown=tester:tester run.sh probe.py /opt/pilcrow-test/
ENTRYPOINT ["bash", "/opt/pilcrow-test/run.sh"]
