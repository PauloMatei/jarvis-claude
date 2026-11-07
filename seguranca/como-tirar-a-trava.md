# Como tirar ou ajustar a trava de confirmação do JARVIS

A trava é o arquivo hooks/trava.py. O server.py a liga a cada pedido (hook PreToolUse com matcher "*"). Não está no settings.json: quem a liga é o servidor.

## Opção 1: ajustar o que é barrado (recomendado)
1. Abra hooks/trava.py, dentro da pasta do JARVIS.
2. Ajuste a lista de ações que pedem confirmação.
3. Salve e reinicie o servidor do JARVIS.

## Opção 2: desligar de vez
1. Abra server.py e procure onde o hook da trava é montado (procure por "trava.py").
2. Troque a definição desses ajustes por: ajustes = {}
   (ou apague o hook e confira que o código logo abaixo continua funcionando).
3. Salve e reinicie o servidor.

## Cuidados
- Sem a trava, o JARVIS publica, envia mensagem, apaga e mexe em dados sem perguntar.
- Faça uma cópia de server.py e trava.py antes de editar.
- Para voltar atrás, restaure a cópia.
- Hoje a trava pede "Confirma?" e só abre depois de "confirmo", "pode" ou "sim", e só para aquele pedido.
