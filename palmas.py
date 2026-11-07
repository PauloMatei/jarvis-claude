"""palmas.py: fica escutando o microfone; duas palmas seguidas abrem o JARVIS.

Ao detectar as palmas ele:
  1. liga o servidor (python server.py) se ainda não estiver no ar, numa janela minimizada;
  2. abre o JARVIS numa janela própria (Edge ou Chrome) com o som já liberado.
A escuta por voz (sem apertar espaço) é feita pela própria tela do JARVIS.

Variáveis de ambiente (opcionais):
  JARVIS_PALMAS_LIMITE  sensibilidade da palma, de 0.05 a 0.95 (padrão 0.30; MENOR = mais sensível)
  JARVIS_MIC            número ou parte do nome do microfone (veja com: python palmas.py --mics)
  JARVIS_NAVEGADOR      caminho do navegador, se ele não achar o Edge ou o Chrome sozinho
  JARVIS_PORTA          porta do servidor (padrão: a do .env, ou 8790)

Comandos:
  python palmas.py              fica escutando
  python palmas.py --calibrar   mostra o volume de cada batida, sem abrir nada (pra ajustar o limite)
  python palmas.py --mics       lista os microfones
"""
import os
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from collections import deque
from pathlib import Path

import numpy as np

PASTA = Path(__file__).resolve().parent
TAXA = 16000
BLOCO_S = 0.02                       # blocos de 20 ms
BLOCO = int(TAXA * BLOCO_S)
INTERVALO_MIN, INTERVALO_MAX = 0.15, 1.0   # tempo entre as duas palmas (segundos)
PAUSA_APOS_ABRIR = 4.0               # depois de abrir, ignora palmas por alguns segundos
AQUECER_S = 1.0                      # ao ligar, aprende o barulho do ambiente antes de aceitar palmas
BRILHO_MIN = 0.20                    # parte da energia acima de 1,5 kHz: palma tem muita, voz tem pouca


class DetectorPalmas:
    """Recebe blocos de áudio e avisa quando houver duas palmas seguidas.

    Uma palma é um estalo forte que vem depois de um trecho mais quieto e some rápido.
    Voz e música (sons que continuam) não contam.
    """

    def __init__(self, limite=0.30):
        self.limite = limite
        self.historico = deque(maxlen=3)   # volume (rms) dos 3 blocos anteriores
        self.pendente = None               # palma candidata esperando confirmar que o som sumiu
        self.palmas = []                   # instantes das palmas confirmadas
        self.livre_apos = 0.0              # intervalo mínimo entre candidatas
        self.mudo_ate = 0.0
        self.fundo = None                  # volume médio do ambiente (ruído de fundo), aprendido aos poucos
        self.aquecer_ate = AQUECER_S       # no começo ele só aprende o barulho do ambiente

    def processar(self, bloco, t, relatorio=None):
        """Devolve True quando detectar a dupla de palmas. `relatorio(texto)` recebe mensagens de calibração."""
        pico = float(np.max(np.abs(bloco)))
        rms = float(np.sqrt(np.mean(np.square(bloco))))
        achou = False
        if self.fundo is None:
            self.fundo = rms
        if t >= self.mudo_ate and t >= self.aquecer_ate:
            if self.pendente is not None:
                p = self.pendente
                p["n"] += 1
                if p["n"] in (3, 4):
                    p["depois"].append(rms)
                if p["n"] == 4:
                    self.pendente = None
                    if max(p["depois"]) < 0.6 * p["pico"]:   # o som sumiu rápido: foi palma
                        self.palmas.append(p["t"])
                        if relatorio:
                            relatorio(f"  palma confirmada (pico {p['pico']:.2f})")
                        achou = self._dupla(t)
                    elif relatorio:
                        relatorio(f"  descartado: o som continuou (pico {p['pico']:.2f}); parece voz ou música")
            elif t >= self.livre_apos and pico >= self.limite:
                antes = max(self.historico) if self.historico else 0.0
                brilho = self._brilho(bloco)               # palma é estalo agudo; voz concentra nos graves
                if antes < 0.3 * pico and pico >= 6 * self.fundo and brilho >= BRILHO_MIN:   # de repente, acima do ambiente
                    self.pendente = {"t": t, "pico": pico, "n": 0, "depois": []}
                    self.livre_apos = t + 0.12
                elif relatorio:
                    motivo = ("soou grave demais pra ser palma (parece voz)" if brilho < BRILHO_MIN
                              else "não se destacou do barulho ao redor")
                    relatorio(f"  descartado: {motivo} (pico {pico:.2f}, brilho {brilho:.2f})")
        if self.pendente is None and pico < self.limite:
            self.fundo += 0.02 * (rms - self.fundo)    # aprende o barulho do ambiente
        self.historico.append(rms)
        return achou

    @staticmethod
    def _brilho(bloco):
        """Fração da energia acima de 1,5 kHz (0 = só grave, 1 = só agudo)."""
        espectro = np.abs(np.fft.rfft(bloco * np.hanning(len(bloco)))) ** 2
        corte = int(1500 / (TAXA / len(bloco)))
        return float(espectro[corte:].sum() / (espectro.sum() + 1e-12))

    def _dupla(self, t):
        self.palmas = [x for x in self.palmas if t - x <= INTERVALO_MAX + 0.2]
        if len(self.palmas) >= 2 and INTERVALO_MIN <= self.palmas[-1] - self.palmas[-2] <= INTERVALO_MAX:
            self.palmas.clear()
            self.mudo_ate = t + PAUSA_APOS_ABRIR
            self.pendente = None
            return True
        return False


# ---------------------------------------------------------------- abrir o JARVIS
def ler_env():
    valores = {}
    try:
        for linha in (PASTA / ".env").read_text(encoding="utf-8-sig").splitlines():
            if "=" in linha and not linha.lstrip().startswith("#"):
                k, v = linha.split("=", 1)
                valores[k.strip()] = v.strip()
    except OSError:
        pass
    return valores


PORTA = int(os.environ.get("JARVIS_PORTA") or ler_env().get("JARVIS_PORTA") or 8790)
URL = f"http://localhost:{PORTA}"


def porta_aberta():
    try:
        with socket.create_connection(("127.0.0.1", PORTA), timeout=0.3):
            return True
    except OSError:
        return False


def achar_navegador():
    candidatos = [
        os.environ.get("JARVIS_NAVEGADOR"),
        shutil.which("msedge"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        shutil.which("chrome"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for c in candidatos:
        if c and Path(c).exists():
            return c
    return None


class Abridor:
    def __init__(self):
        self.servidor = None
        self.janela = None
        self.ultima = -1e9

    def abrir(self):
        if time.monotonic() - self.ultima < 30:   # palmas repetidas logo depois de abrir não abrem outra janela
            print("Ignorado: o JARVIS acabou de abrir.")
            return
        self.ultima = time.monotonic()
        if not porta_aberta():
            python = sys.executable
            if python.lower().endswith("pythonw.exe"):
                python = python[:-len("pythonw.exe")] + "python.exe"
            kwargs = {}
            if os.name == "nt":
                info = subprocess.STARTUPINFO()
                info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                info.wShowWindow = 6  # minimizada (você pode fechar essa janela pra desligar o JARVIS)
                kwargs = {"creationflags": subprocess.CREATE_NEW_CONSOLE, "startupinfo": info}
            # --sem-abrir: o servidor não abre o navegador (quem abre é este programa, com o som liberado)
            self.servidor = subprocess.Popen([python, "server.py", "--sem-abrir"], cwd=str(PASTA), **kwargs)
            for _ in range(80):  # até ~40 s pro servidor subir
                if porta_aberta():
                    break
                time.sleep(0.5)
            else:
                print("O servidor não subiu em 40 s. Rode 'python server.py' à mão pra ver o erro.")
                return
        if self.janela is not None and self.janela.poll() is None:
            print("O JARVIS já está aberto.")
            return
        navegador = achar_navegador()
        if not navegador:
            print("Não achei o Edge nem o Chrome; abrindo no navegador padrão (talvez precise clicar uma vez na página).")
            webbrowser.open(URL)
            return
        perfil = Path(os.environ.get("LOCALAPPDATA", str(PASTA))) / "JarvisPerfil"
        self.janela = subprocess.Popen([
            navegador, f"--app={URL}", f"--user-data-dir={perfil}",
            "--autoplay-policy=no-user-gesture-required",   # som liberado sem precisar clicar
            "--no-first-run", "--no-default-browser-check", "--window-size=900,700",
        ])


# ---------------------------------------------------------------- microfone
def escolher_microfone(sd):
    pedido = os.environ.get("JARVIS_MIC")
    if not pedido:
        return None
    if pedido.isdigit():
        return int(pedido)
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and pedido.lower() in d["name"].lower():
            return i
    print(f"Microfone '{pedido}' não encontrado; usando o padrão do Windows.")
    return None


def abrir_fluxo(sd, dispositivo):
    """Abre o microfone em modo 'callback' (funciona com todos os drivers do Windows, inclusive WDM-KS).

    Tenta 16 kHz mono; se o driver não aceitar, usa a taxa e os canais nativos do dispositivo.
    Devolve (fila de blocos mono float32, fluxo, taxa).
    """
    import queue
    info = sd.query_devices(dispositivo if dispositivo is not None else sd.default.device[0], "input")
    nativa = int(info["default_samplerate"]) or 48000
    max_canais = max(1, int(info["max_input_channels"]))
    tentativas = [(TAXA, 1), (nativa, 1), (nativa, min(2, max_canais)), (nativa, max_canais)]
    ultimo_erro = None
    for taxa, canais in tentativas:
        fila = queue.Queue(maxsize=200)

        def ao_receber(dados, quadros, tempo, status, fila=fila):
            try:
                fila.put_nowait(dados.mean(axis=1).astype(np.float32).copy())
            except queue.Full:
                pass

        try:
            fluxo = sd.InputStream(device=dispositivo, channels=canais, samplerate=taxa,
                                   blocksize=int(taxa * BLOCO_S), dtype="float32", callback=ao_receber)
            return fila, fluxo, taxa
        except Exception as erro:
            ultimo_erro = erro
    raise ultimo_erro


def bip():
    try:
        import winsound
        winsound.Beep(880, 120)
    except Exception:
        pass


def escutar(calibrar=False):
    try:
        import sounddevice as sd
    except ImportError:
        print("Falta instalar o sounddevice. Rode:  python -m pip install --user sounddevice numpy")
        sys.exit(1)

    limite = float(os.environ.get("JARVIS_PALMAS_LIMITE", "0.30"))
    detector = DetectorPalmas(limite)
    abridor = Abridor()
    print(f"Escutando. Bata duas palmas pra abrir o JARVIS (sensibilidade {limite}). Ctrl+C pra sair.")
    if calibrar:
        print("Modo calibração: nada será aberto. Bata palmas e veja os números.")
    while True:
        try:
            fila, fluxo, taxa = abrir_fluxo(sd, escolher_microfone(sd))
            with fluxo:
                if calibrar:
                    print(f"Microfone aberto ({taxa} Hz). A cada segundo mostro o som mais alto que chegou.")
                inicio = time.monotonic()
                resto = np.zeros(0, dtype=np.float32)
                maior_seg, prox_relato = 0.0, 1.0
                while True:
                    dados = fila.get(timeout=5)  # sem áudio por 5 s: o microfone parou
                    if taxa != TAXA:  # converte pra 16 kHz
                        n = max(1, int(round(len(dados) * TAXA / taxa)))
                        dados = np.interp(np.linspace(0, len(dados) - 1, n), np.arange(len(dados)), dados).astype(np.float32)
                    resto = np.concatenate([resto, dados])
                    while len(resto) >= BLOCO:
                        bloco, resto = resto[:BLOCO], resto[BLOCO:]
                        t = time.monotonic() - inicio
                        pico = float(np.max(np.abs(bloco)))
                        if calibrar:
                            maior_seg = max(maior_seg, pico)
                            if t >= prox_relato:
                                print(f"nível no último segundo: {maior_seg:.3f}" + ("   (silêncio total: o microfone não está mandando som)" if maior_seg < 0.001 else ""))
                                maior_seg, prox_relato = 0.0, t + 1.0
                            if pico >= 0.1:
                                print(f"volume {pico:.2f}" + ("  <- passa do limite" if pico >= limite else ""))
                        if detector.processar(bloco, t, relatorio=print if calibrar else None):
                            print("Duas palmas! Abrindo o JARVIS..." if not calibrar else "DUAS PALMAS detectadas.")
                            if not calibrar:
                                bip()
                                abridor.abrir()
        except KeyboardInterrupt:
            print("Encerrado.")
            return
        except Exception as erro:  # microfone desligado ou trocado: tenta de novo
            print(f"Microfone indisponível ({erro}). Tentando de novo em 5 s...")
            time.sleep(5)


if __name__ == "__main__":
    if "--mics" in sys.argv:
        import sounddevice as sd
        apis = sd.query_hostapis()
        padrao = sd.default.device[0]
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                marca = "  <- padrão do Windows" if i == padrao else ""
                print(f"  [{i}] {d['name']}  ({apis[d['hostapi']]['name']}){marca}")
    else:
        escutar(calibrar="--calibrar" in sys.argv)
