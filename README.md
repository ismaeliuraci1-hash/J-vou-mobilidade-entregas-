# Protótipo Jávou — cadastro e login

## Executar localmente

Dentro desta pasta, rode:

```bash
python3 server.py
```

Depois abra `http://127.0.0.1:18080` no navegador.

Para ativar a integração opcional com Mercado Pago no servidor, configure a variável de ambiente `MERCADOPAGO_ACCESS_TOKEN`. Sem essa variável, o sistema permanece corretamente em modo sandbox.

## O que foi implementado

- API local em Python usando apenas a biblioteca padrão.
- Banco SQLite criado automaticamente.
- Cadastro de cliente, entregador e motorista.
- Login por celular ou e-mail.
- Senha armazenada com PBKDF2-SHA256, nunca em texto puro.
- Token de sessão salvo no navegador.
- Cidades-piloto: Belo Horizonte - MG e Cascavel - PR.
- Perfil de parceiro pendente de aprovação; cliente entra como ativo.
- Tela inicial, entrega, mobilidade, cadastro e login integradas à API.
- Painel administrativo local com estatísticas, listagem e aprovação/bloqueio.
- Chave local do painel: `javou-dev-admin`.
- Mapa Leaflet com OpenStreetMap nas duas cidades-piloto.
- Captura da localização do navegador com consentimento.
- Endpoint autenticado para registrar posição e endpoint administrativo para posições ativas.
- Criação de entregas e corridas com preço estimado.
- Distribuição automática para o entregador/motorista ativo mais próximo, por cidade e raio de até 25 km.
- Consulta e atualização de status do serviço.
- Área do parceiro com online/offline, atualização de GPS, ofertas e mudança de status.
- Distribuição considera somente parceiro aprovado e online.
- Tela de acompanhamento com mapa e atualização automática a cada 3 segundos.
- Endpoint de acompanhamento retorna status, profissional e última localização do serviço.
- Central de notificações vinculada ao cliente e ao parceiro.
- Registro de método de pagamento e geração de código Pix de sandbox.
- Alertas do navegador com permissão do usuário e consulta automática de novas notificações.
- Adaptador opcional para Pix real via Mercado Pago usando `MERCADOPAGO_ACCESS_TOKEN` no servidor.
- Comissão da plataforma configurada em 10% do valor bruto.
- Repasse previsto ao profissional: 90% do valor bruto.
- Chave Pix da plataforma configurada como `ismaeliuraci1@gmail.com` (altere com `JAVOU_PLATFORM_PIX_KEY` antes da produção).
- Painel mostra a receita acumulada da plataforma.

## Próximas integrações para produção

- Banco PostgreSQL gerenciado.
- Verificação de telefone/e-mail.
- Upload e análise de documentos.
- GPS real e mapa Google Maps/Mapbox.
- WebSocket para localização ao vivo.
- Pix/cartão com provedor brasileiro e webhooks.
- Notificações push.
- LGPD, termos de uso, política de privacidade e auditoria.

## Publicação do web app

Foram incluídos `Dockerfile` e `render.yaml` para preparar uma publicação de teste em um serviço de hospedagem compatível com Docker. O banco SQLite é adequado somente para demonstração; em produção deve ser substituído por PostgreSQL.

Este é um protótipo funcional local; não deve ser usado em produção sem as integrações, testes e revisão de segurança adequados.
