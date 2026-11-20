# A20 X Battery Widget

Widget para a barra de tarefas do Windows 11 que mostra a bateria do headset **Logitech ASTRO A20 X** e de um **mouse Logitech LIGHTSPEED** (testado com o G305), no estilo dos ícones do próprio Windows.

| Fechado | Mouse por cima |
| --- | --- |
| ![Widget fechado](docs/collapsed.png) | ![Widget aberto](docs/expanded.png) |

## Como funciona

- Fica logo à esquerda da bandeja (relógio e ícones), mostrando só os ícones.
- **Passar o mouse por cima** (0,4 s) abre as porcentagens com uma animação; fecha quando o mouse sai.
- **Clique** fixa aberto; outro clique desfixa.
- **Clique direito**: atualizar agora / sair.
- Raio verde = carregando. Com 15% ou menos, a porcentagem fica sempre visível, em vermelho.
- Some sozinho quando um app está em tela cheia.
- Atualiza a cada 60 segundos (e na hora em que abre).
- Funciona com o A20 X **no cabo e no sem fio**, e troca sozinho quando você pluga ou tira o cabo.

Não precisa do G HUB fechado: o widget conversa direto com o headset pelo USB, junto com ele.

## Instalação

Precisa do Python para Windows (testado no 3.14) e da fonte Segoe Fluent Icons, que já vem no Windows 11.

```powershell
git clone https://github.com/TaGato-Fiuza/a20x-battery-widget.git
cd a20x-battery-widget
python -m pip install -r requirements.txt
pythonw battery_widget.py
```

### Abrir junto com o Windows

```powershell
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut("$([Environment]::GetFolderPath('Startup'))\A20X Battery Widget.lnk")
$s.TargetPath = (Get-Command pythonw).Source
$s.Arguments = "`"$PWD\battery_widget.py`""
$s.WorkingDirectory = "$PWD"
$s.Save()
```

Para parar de abrir junto, apague o atalho "A20X Battery Widget" da pasta Inicializar (`shell:startup`).

## Configuração

No começo do [`battery_widget.py`](battery_widget.py):

| Constante | Padrão | O que faz |
| --- | --- | --- |
| `POLL_SECONDS` | 60 | intervalo entre leituras |
| `LOW_BATTERY` | 15 | abaixo disso o número fica sempre visível, em vermelho |
| `HOVER_OPEN_SECONDS` | 0.4 | tempo com o mouse parado em cima para abrir |
| `HOVER_CLOSE_SECONDS` | 0.5 | tempo depois que o mouse sai para fechar |
| `ANIMATION_SECONDS` | 0.22 | duração da animação |

## Protocolo

O A20 X usa o protocolo **Centurion** da Logitech (o mesmo do G522 e do G PRO X 2/3): relatórios HID de 64 bytes com prefixo `50 23` na usage page `0xFFA0`. A bateria é a feature `0x0104`; a resposta da função 0 é `[nível %, ?, estado]`, com estado 1 ou 2 = carregando.

| Modo | PID | Interface | Como chegar na bateria |
| --- | --- | --- | --- |
| Sem fio (transmissor, "A20 X HID") | `0x0B35` | 5 | pela ponte `0x0003` (índice 3) até o fone |
| Cabo ("A20 X - USB wired mode") | `0x0B2E` | 3 | direto, sem ponte |

Com o fone no cabo, o transmissor continua aparecendo no USB, então o widget tenta o cabo primeiro.

O mouse usa **HID++ 2.0** pelo receptor LIGHTSPEED (device index 1), com a feature `0x1004` (Unified Battery) ou `0x1000` (Battery Status). O G305 informa a bateria em degraus (90%, 50%, 20%...).

O suporte ao A20 X também foi enviado para o [HeadsetControl](https://github.com/Sapd/HeadsetControl/pull/641), que é usado por outros apps de bateria.

## Arquivos

- [`battery_widget.py`](battery_widget.py): o widget (tkinter + Win32).
- [`devices.py`](devices.py): leitura da bateria do A20 X e do mouse.

## Licença

[MIT](LICENSE)
