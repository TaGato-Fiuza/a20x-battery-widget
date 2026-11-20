"""Leitura de bateria do Logitech ASTRO A20 X e de mouses Logitech LIGHTSPEED.

A20 X: protocolo Centurion (HID, usage page 0xFFA0, prefixo 50 23), feature
0x0104 (Battery SOC). No cabo (PID 0B2E) a feature responde direto; no sem fio
(PID 0B35, o transmissor) ela fica no fone, atras da ponte 0x0003.
Resposta fn0: [nivel%, ?, estado] onde estado 1/2 = carregando.

Mouse: HID++ 2.0 pelo receptor LIGHTSPEED (device index 1), feature 0x1004
(Unified Battery) ou 0x1000 (Battery Status).
"""
import hid

VID = 0x046D
PIDS = (0x0B2E, 0x0B35, 0x0B34)  # cabo, transmissor sem fio, (0B34: visto em outro relato)
PREFIX = [0x50, 0x23]
FEATURE_BATTERY = 0x0104


class A20X:
    def __init__(self):
        self.dev = None
        self.battery_index = None
        self.bridge_index = None

    @staticmethod
    def _candidates():
        """Caminhos HID possiveis, o cabo (0B2E) primeiro.

        Com o fone no cabo, o transmissor sem fio (0B35) continua plugado e aparece
        tambem, mas o fone nao responde por ele; por isso tenta cada um em ordem.
        """
        found = [i for i in hid.enumerate(VID, 0)
                 if i["product_id"] in PIDS and i["usage_page"] == 0xFFA0]
        found.sort(key=lambda i: PIDS.index(i["product_id"]))
        return [i["path"] for i in found]

    def _open(self, path):
        d = hid.device()
        d.open_path(path)
        self.dev = d
        self.battery_index = None
        self.bridge_index = None

    def close(self):
        if self.dev:
            try:
                self.dev.close()
            except Exception:
                pass
        self.dev = None

    def _xfer(self, payload):
        while self.dev.read(64, 20):
            pass
        frame = PREFIX + [len(payload) + 1, 0] + list(payload)
        if self.dev.write(frame + [0] * (64 - len(frame))) < 0:
            raise IOError("falha ao escrever")
        for _ in range(8):
            r = self.dev.read(64, 500)
            if not r:
                break
            if r[:2] != PREFIX:
                continue
            pl = r[4:3 + r[2]]
            if pl and pl[0] == 0xFF and len(pl) > 1 and pl[1] == payload[0]:
                raise IOError(f"erro do headset {bytes(pl).hex(' ')}")
            if pl and pl[0] == payload[0] and len(pl) > 1 and (pl[1] & 0xF0) == (payload[1] & 0xF0):
                return pl[2:]
        raise TimeoutError("sem resposta")

    def _bridge_xfer(self, sub_index, function, params=()):
        """Pedido ao fone atraves da ponte do transmissor (modo sem fio).

        Envia [ponte, 0x11, tamanho] + [00, indice, fn|sw]; a ponte responde um ACK
        (ponte, 0x11) e depois o evento (ponte, 0x10, tam, 00, indice, fn|sw, params...).
        """
        sub = [0x00, sub_index, (function & 0xF0) | 0x01] + list(params)
        payload = [self.bridge_index, 0x11, (len(sub) >> 8) & 0x0F, len(sub) & 0xFF] + sub
        while self.dev.read(64, 20):
            pass
        frame = PREFIX + [len(payload) + 1, 0] + payload
        if self.dev.write(frame + [0] * (64 - len(frame))) < 0:
            raise IOError("falha ao escrever")
        for _ in range(8):
            r = self.dev.read(64, 500)
            if not r:
                break
            if r[:2] != PREFIX:
                continue
            pl = r[4:3 + r[2]]
            if len(pl) >= 4 and pl[0] == self.bridge_index and pl[1] == 0x00 and pl[3] == 0:
                raise IOError("fone desligado")  # evento de desconexao da ponte
            if pl and pl[0] == 0xFF:
                raise IOError(f"ponte recusou {bytes(pl).hex(' ')}")
            if len(pl) >= 7 and pl[0] == self.bridge_index and pl[1] == 0x10 and pl[4] == 0x00:
                if pl[5] == 0xFF:
                    raise IOError(f"fone recusou {bytes(pl).hex(' ')}")
                if pl[5] == sub_index:
                    return pl[7:]
        raise TimeoutError("sem resposta do fone")

    def _detect(self):
        """No cabo (0B2E) a bateria responde direto; no sem fio (0B35) fica atras da ponte."""
        try:
            r = self._xfer([0x00, 0x01, 0x00, 0x03])  # Root.getFeature(ponte)
            self.bridge_index = r[0] if r and r[0] else None
        except IOError:
            self.bridge_index = None  # recusado: sem ponte (modo cabo)
        if self.bridge_index:
            r = self._bridge_xfer(0x00, 0x00, [FEATURE_BATTERY >> 8, FEATURE_BATTERY & 0xFF])
        else:
            r = self._xfer([0x00, 0x01, FEATURE_BATTERY >> 8, FEATURE_BATTERY & 0xFF])
        if not r or r[0] == 0:
            raise IOError("feature de bateria nao encontrada")
        self.battery_index = r[0]

    def _read_open_device(self):
        if self.battery_index is None:
            self._detect()
        if self.bridge_index:
            r = self._bridge_xfer(self.battery_index, 0x00)
        else:
            r = self._xfer([self.battery_index, 0x01])
        level = r[0]
        if level > 100:
            raise IOError(f"nivel invalido {level}")
        charging = len(r) >= 3 and r[2] in (1, 2)
        return level, charging

    def read_battery(self):
        """Retorna (nivel, carregando).

        Usa o caminho que funcionou da ultima vez; se falhar (cabo tirado ou
        colocado), procura de novo entre cabo e transmissor.
        """
        if self.dev is not None:
            try:
                return self._read_open_device()
            except Exception:
                self.close()
        error = IOError("A20 X nao encontrado")
        for path in self._candidates():
            try:
                self._open(path)
                return self._read_open_device()
            except Exception as e:
                error = e
                self.close()
        raise error


class LogitechMouse:
    """Bateria do mouse via HID++ 2.0 no receptor LIGHTSPEED (device index 1)."""

    RECEIVER_PIDS = (0xC53F, 0xC539, 0xC547, 0xC54D)
    SW = 0x0A

    def __init__(self):
        self.devs = []
        self.feature = None  # (feature_id, index)

    def close(self):
        for d in self.devs:
            try:
                d.close()
            except Exception:
                pass
        self.devs = []
        self.feature = None

    def _open(self):
        infos = [i for i in hid.enumerate(0x046D, 0)
                 if i["product_id"] in self.RECEIVER_PIDS and i["usage_page"] == 0xFF00]
        if not infos:
            raise IOError("receptor nao encontrado")
        for i in infos:
            d = hid.device()
            d.open_path(i["path"])
            self.devs.append((i["usage"], d))

    def _req(self, feat, fn, params=()):
        long_dev = dict(self.devs).get(2) or self.devs[0][1]
        msg = [0x11, 0x01, feat, (fn & 0xF0) | self.SW] + list(params)
        for _, d in self.devs:
            while d.read(64, 5):
                pass
        long_dev.write(msg + [0] * (20 - len(msg)))
        for _ in range(15):
            for _, d in self.devs:
                r = d.read(64, 80)
                if not r or r[1] != 0x01:
                    continue
                if r[2] == 0x8F or (r[2] == 0xFF and r[3] == feat):
                    raise IOError("mouse desligado ou erro")
                if r[2] == feat and (r[3] & 0x0F) == self.SW:
                    return r[4:]
        raise TimeoutError("sem resposta do mouse")

    def read_battery(self):
        for attempt in range(2):
            try:
                if not self.devs:
                    self._open()
                if self.feature is None:
                    for fid in (0x1004, 0x1000):
                        r = self._req(0x00, 0x00, [fid >> 8, fid & 0xFF])
                        if r[0]:
                            self.feature = (fid, r[0])
                            break
                    else:
                        raise IOError("mouse sem feature de bateria")
                fid, idx = self.feature
                if fid == 0x1004:  # Unified Battery: getStatus
                    r = self._req(idx, 0x10)
                    return r[0], r[2] in (1, 2)
                r = self._req(idx, 0x00)  # Battery Status
                return r[0], r[2] in (1, 2, 3)
            except Exception:
                self.close()
                if attempt:
                    raise
