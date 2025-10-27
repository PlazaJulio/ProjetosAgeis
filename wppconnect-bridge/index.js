import wppconnect from '@wppconnect-team/wppconnect';
import axios from 'axios';
import 'dotenv/config';

const FLASK_URL = process.env.FLASK_URL || 'http://127.0.0.1:8080/whatsapp';

function extractTwimlMessage(twiml) {
  const m = String(twiml || '').match(/<Message>([\s\S]*?)<\/Message>/i);
  return m ? m[1] : null;
}

wppconnect.create({
  session: 'onboardly',
  headless: true,          // se precisar debugar, mude para false
  useChrome: true,
  autoClose: 0,
  // Se der erro de Chrome no macOS, descomente abaixo e ajuste o caminho:
  // browserPathExecutable: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  catchQR: (_base64, asciiQR) => {
    console.log('--- QR CODE ---\n' + asciiQR + '\n---------------');
  }
}).then((client) => {
  console.log('✅ WPPConnect iniciado. Aguardando mensagens…');

  client.onMessage(async (msg) => {
    try {
      const body = msg.body || '';
      const from = `whatsapp:+${(msg.from || '').replace(/\D/g, '')}`;

      // Envia para o Flask como se fosse o Twilio (form-data Body/From)
      const params = new URLSearchParams();
      params.append('Body', body);
      params.append('From', from);

      const resp = await axios.post(FLASK_URL, params, {
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        timeout: 20000
      });

      const answer = extractTwimlMessage(resp?.data) || 'Sem resposta no momento.';
      await client.sendText(msg.from, answer);
    } catch (err) {
      console.error('Erro ao chamar Flask:', err?.response?.data || err.message);
      await client.sendText(msg.from, 'Falha temporária ao processar sua mensagem.');
    }
  });
}).catch((e) => console.error('Erro ao iniciar WPPConnect:', e));