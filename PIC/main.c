#include <xc.h>
#include <stdbool.h>
#include <stdint.h>

// PIC16F877A + AD8232 -> CP2102 -> PC
// Compilador: MPLAB XC8
// Cristal: 20 MHz
//
// Formato de salida UART (115200 8N1):
//   # ECG_START
//   # SAMPLE_RATE=360
//   # COLUMNS: time_ms,ecg_raw,ecg_volt,lead_ok
//   time_ms,ecg_raw,ecg_volt,lead_ok
//
// Conexion recomendada:
//   AD8232 OUT  -> RA0 / AN0
//   AD8232 LO+  -> RB0
//   AD8232 LO-  -> RB1
//   RC6 / TX    -> RX del CP2102
//   GND comun entre PIC, AD8232 y CP2102

#define _XTAL_FREQ              20000000UL

// CONFIG
#pragma config FOSC = HS
#pragma config WDTE = OFF
#pragma config PWRTE = ON
#pragma config BOREN = ON
#pragma config LVP = OFF
#pragma config CPD = OFF
#pragma config WRT = OFF
#pragma config CP = OFF

#define SAMPLE_RATE_HZ          360U
#define VREF_MV                 5000UL
#define TIMER1_RELOAD           63800U

#define LO_POS_PORT             RB0
#define LO_NEG_PORT             RB1

static volatile bool sample_due = false;

static uint32_t time_ms = 0UL;
static uint16_t time_remainder = 0U;

static void gpio_init(void);
static void adc_init(void);
static void uart_init(void);
static void timer1_init(void);
static uint16_t adc_read_an0(void);
static void uart_write_byte(uint8_t value);
static void uart_write_text(const char *text);
static void uart_write_crlf(void);
static void uart_write_u16(uint16_t value);
static void uart_write_u32(uint32_t value);
static void uart_write_voltage_4dp(uint16_t adc_raw);
static void send_header(void);
static void send_sample_csv(uint32_t timestamp_ms, uint16_t adc_raw, bool lead_ok);
static void update_timestamp(void);

void __interrupt() isr(void) {
    if (PIR1bits.TMR1IF) {
        PIR1bits.TMR1IF = 0;
        TMR1H = (uint8_t)(TIMER1_RELOAD >> 8);
        TMR1L = (uint8_t)(TIMER1_RELOAD & 0xFF);
        sample_due = true;
    }
}

void main(void) {
    uint16_t ecg_raw = 0U;
    bool lead_ok = false;

    gpio_init();
    adc_init();
    uart_init();
    timer1_init();

    __delay_ms(1000);
    send_header();

    INTCONbits.PEIE = 1;
    INTCONbits.GIE = 1;

    while (1) {
        if (!sample_due) {
            continue;
        }

        sample_due = false;
        update_timestamp();

        ecg_raw = adc_read_an0();
        lead_ok = !(LO_POS_PORT || LO_NEG_PORT);

        send_sample_csv(time_ms, ecg_raw, lead_ok);
    }
}

static void gpio_init(void) {
    TRISA = 0x01;   // RA0 como entrada analogica
    TRISB = 0x03;   // RB0/RB1 como entradas digitales para LO+/LO-
    TRISC = 0x80;   // RC6=TX salida, RC7=RX entrada

    PORTA = 0x00;
    PORTB = 0x00;
    PORTC = 0x00;
}

static void adc_init(void) {
    ADCON1bits.PCFG3 = 1;
    ADCON1bits.PCFG2 = 1;
    ADCON1bits.PCFG1 = 1;
    ADCON1bits.PCFG0 = 0;   // AN0 analogico, resto digital
    ADCON1bits.ADFM = 1;    // resultado justificado a la derecha

    ADCON0bits.CHS2 = 0;
    ADCON0bits.CHS1 = 0;
    ADCON0bits.CHS0 = 0;    // canal AN0
    ADCON0bits.ADCS1 = 1;
    ADCON0bits.ADCS0 = 0;   // Fosc/32
    ADCON0bits.ADON = 1;

    __delay_us(20);
}

static void uart_init(void) {
    TRISCbits.TRISC6 = 0;
    TRISCbits.TRISC7 = 1;

    TXSTAbits.SYNC = 0;     // modo asincrono
    TXSTAbits.BRGH = 1;     // alta velocidad
    SPBRG = 10;             // 20 MHz -> 113636 bps (~1.36% error)

    RCSTAbits.SPEN = 1;
    RCSTAbits.CREN = 1;
    TXSTAbits.TXEN = 1;
}

static void timer1_init(void) {
    T1CONbits.TMR1CS = 0;   // reloj interno Fosc/4
    T1CONbits.T1CKPS1 = 1;
    T1CONbits.T1CKPS0 = 1;  // prescaler 1:8
    T1CONbits.T1OSCEN = 0;

    TMR1H = (uint8_t)(TIMER1_RELOAD >> 8);
    TMR1L = (uint8_t)(TIMER1_RELOAD & 0xFF);

    PIR1bits.TMR1IF = 0;
    PIE1bits.TMR1IE = 1;
    T1CONbits.TMR1ON = 1;
}

static uint16_t adc_read_an0(void) {
    ADCON0bits.GO_DONE = 1;
    while (ADCON0bits.GO_DONE) {
        ;
    }
    return ((uint16_t)ADRESH << 8) | ADRESL;
}

static void uart_write_byte(uint8_t value) {
    while (!PIR1bits.TXIF) {
        ;
    }
    TXREG = value;
}

static void uart_write_text(const char *text) {
    while (*text != '\0') {
        uart_write_byte((uint8_t)*text);
        text++;
    }
}

static void uart_write_crlf(void) {
    uart_write_byte('\r');
    uart_write_byte('\n');
}

static void uart_write_u16(uint16_t value) {
    char buffer[5];
    uint8_t index = 0U;

    if (value == 0U) {
        uart_write_byte('0');
        return;
    }

    while (value > 0U) {
        buffer[index++] = (char)('0' + (value % 10U));
        value /= 10U;
    }

    while (index > 0U) {
        uart_write_byte((uint8_t)buffer[--index]);
    }
}

static void uart_write_u32(uint32_t value) {
    char buffer[10];
    uint8_t index = 0U;

    if (value == 0UL) {
        uart_write_byte('0');
        return;
    }

    while (value > 0UL) {
        buffer[index++] = (char)('0' + (value % 10UL));
        value /= 10UL;
    }

    while (index > 0U) {
        uart_write_byte((uint8_t)buffer[--index]);
    }
}

static void uart_write_voltage_4dp(uint16_t adc_raw) {
    uint32_t value_10000 = ((uint32_t)adc_raw * 50000UL + 511UL) / 1023UL;
    uint16_t integer_part = (uint16_t)(value_10000 / 10000UL);
    uint16_t fraction = (uint16_t)(value_10000 % 10000UL);

    uart_write_u16(integer_part);
    uart_write_byte('.');
    uart_write_byte((uint8_t)('0' + (fraction / 1000U) % 10U));
    uart_write_byte((uint8_t)('0' + (fraction / 100U) % 10U));
    uart_write_byte((uint8_t)('0' + (fraction / 10U) % 10U));
    uart_write_byte((uint8_t)('0' + (fraction % 10U)));
}

static void send_header(void) {
    uart_write_text("# ECG_START");
    uart_write_crlf();
    uart_write_text("# SAMPLE_RATE=360");
    uart_write_crlf();
    uart_write_text("# COLUMNS: time_ms,ecg_raw,ecg_volt,lead_ok");
    uart_write_crlf();
}

static void send_sample_csv(uint32_t timestamp_ms, uint16_t adc_raw, bool lead_ok) {
    uart_write_u32(timestamp_ms);
    uart_write_byte(',');
    uart_write_u16(adc_raw);
    uart_write_byte(',');
    uart_write_voltage_4dp(adc_raw);
    uart_write_byte(',');
    uart_write_byte((uint8_t)(lead_ok ? '1' : '0'));
    uart_write_crlf();
}

static void update_timestamp(void) {
    time_ms += 2UL;
    time_remainder += 280U; // 1000 = 360*2 + 280
    if (time_remainder >= 360U) {
        time_remainder -= 360U;
        time_ms += 1UL;
    }
}
