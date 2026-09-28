<?php
/**
 * Plugin Name: CDH Required Accessories
 * Description: Pflicht-Zubehör pro Produkt (Aggregation je SKU, nicht entfernbar, Zubehör am Warenkorb-Ende). Stand 2.4 + 2. feste Metaboxen (A & B). Ab 2.5: Warenkorb-Automatik abschaltbar (WooCommerce → Einstellungen → Produkte) — dann ergänzt der TEXMA-WEX-Import das Zubehör für CDH und der Kunde sieht es nirgends. Ab 2.6: Staffelpreise für CDH am Zubehör-Artikel (VK/EK je Menge im CDH-Auftrag), auch im TEXMA-Tool pflegbar. Ab 2.7: vier Plätze A–D und Zubehör am Zubehör (z. B. Druck → Transfer).
 * Author: TEXMA
 * Version: 2.7.0
 * Requires at least: 6.1
 * Requires PHP: 7.4
 * WC requires at least: 8.0
 * WC tested up to: 9.0
 */

if (!defined('ABSPATH')) { exit; }

class CDH_Required_Accessories_24 {
    const META_KEY       = '_cdh_required_accessories'; // array of rows: [accessory_id, qty_per_unit]
    const CART_FLAG      = '_cdh_is_accessory';
    const CART_GROUP_KEY = '_cdh_group_key';
    const OPTION_CART    = 'cdh_ra_warenkorb';           // 'yes' (Standard) | 'no'
    const ORDER_ITEM_FLAG = '_cdh_is_accessory';          // an Bestellpositionen (ab 2.5)
    const STAFFEL_KEY    = '_cdh_staffelpreise';         // am Zubehör-Artikel (ab 2.6): [{ab, vk, ek}]
    const STAFFEL_MAX    = 10;
    const SLOTS          = 4;                             // Plätze A–D (ab 2.7; vorher 2)
    const TIEFE          = 3;                             // Zubehör am Zubehör (ab 2.7), höchstens 3 Ebenen
    const STAFFEL_STANDARD = [1, 10, 25, 50, 100, 250, 500];  // übliche Stufen (2.6.1), vorbelegt

    public function __construct() {
        // Admin UI (feste Plätze A–D)
        add_action('add_meta_boxes',           [$this, 'add_metaboxes']);
        add_action('save_post_product',        [$this, 'save_metaboxes']);
        add_action('save_post_product',        [$this, 'save_staffel']);
        add_action('admin_enqueue_scripts',    [$this, 'enqueue_admin_assets']);
        add_action('wp_ajax_cdh_ra_search',    [$this, 'ajax_search_products']); // Produktsuche

        // Einstellung: WooCommerce → Einstellungen → Produkte (Allgemein)
        add_filter('woocommerce_get_settings_products',    [$this, 'add_settings'], 10, 2);

        if (!self::cart_enabled()) {
            // Warenkorb-Automatik aus: Zubehör kommt nicht in den Warenkorb,
            // in Mails, Rechnungen oder Konto. Der TEXMA-WEX-Import ergänzt es
            // für CDH aus denselben Regeln (Schalter „Pflicht-Zubehör" im Tool).
            // Reste aus Warenkörben von vor dem Umschalten entfernen:
            add_action('woocommerce_cart_loaded_from_session', [$this, 'remove_accessory_lines'], 5);
            add_action('woocommerce_before_calculate_totals',  [$this, 'remove_accessory_lines'], 5);
            return;
        }

        // Cart Sync
        add_action('woocommerce_cart_loaded_from_session', [$this, 'sync_cart']);
        add_action('woocommerce_add_to_cart',              [$this, 'sync_cart']);
        add_action('woocommerce_before_calculate_totals',  [$this, 'sync_cart'], 5);

        // Zubehör am Warenkorb-Ende
        add_action('woocommerce_cart_loaded_from_session', [$this, 'order_accessories_last'], 9999);
        add_action('woocommerce_before_calculate_totals',  [$this, 'order_accessories_last'], 9999);

        // Sperren: Entfernen/Ändern
        add_filter('woocommerce_cart_item_remove_link',    [$this, 'hide_remove_for_accessories'], 10, 2);
        add_filter('woocommerce_cart_item_quantity',       [$this, 'lock_quantity_for_accessories'], 10, 3);
        add_filter('woocommerce_update_cart_validation',   [$this, 'block_qty_change_on_update'], 10, 4);

        // Label im Warenkorb
        add_filter('woocommerce_cart_item_name',           [$this, 'label_cart_item_name'], 10, 3);

        add_filter('woocommerce_add_to_cart_validation',   [$this, 'prevent_direct_add_of_accessory'], 10, 3);

        // Kennzeichen an der Bestellposition (ab 2.5), damit Auswertungen und
        // der Import automatisch hinzugefügtes Zubehör erkennen.
        add_action('woocommerce_checkout_create_order_line_item', [$this, 'flag_order_item'], 10, 4);
    }

    /* ================= Einstellung (ab 2.5) ================= */

    public static function cart_enabled() : bool {
        return get_option(self::OPTION_CART, 'yes') !== 'no';
    }

    public function add_settings($settings, $current_section) {
        if ($current_section !== '') return $settings;
        $settings[] = [
            'title' => __('CDH Pflicht-Zubehör', 'cdh-ra'),
            'type'  => 'title',
            'id'    => 'cdh_ra_settings',
        ];
        $settings[] = [
            'title'   => __('Zubehör im Warenkorb', 'cdh-ra'),
            'desc'    => __('Pflicht-Zubehör automatisch in den Warenkorb legen (0,00 €)', 'cdh-ra'),
            'desc_tip'=> __('Aus: Der Kunde sieht nur seinen Artikel – im Warenkorb, an der Kasse, in Mails und Rechnungen. Der TEXMA-WEX-Import ergänzt das Zubehör für CDH aus denselben Regeln. Vorher im Import-Tool beim Shop „Pflicht-Zubehör ergänzen“ einschalten.', 'cdh-ra'),
            'id'      => self::OPTION_CART,
            'type'    => 'checkbox',
            'default' => 'yes',
        ];
        $settings[] = ['type' => 'sectionend', 'id' => 'cdh_ra_settings'];
        return $settings;
    }

    public function remove_accessory_lines() {
        if (!WC()->cart) return;
        foreach (WC()->cart->get_cart() as $key => $item) {
            if (!empty($item[self::CART_FLAG])) {
                WC()->cart->remove_cart_item($key);
            }
        }
    }

    public function flag_order_item($item, $cart_item_key, $values, $order) {
        if (!empty($values[self::CART_FLAG])) {
            $item->add_meta_data(self::ORDER_ITEM_FLAG, 'yes', true);
        }
    }

    /* ================= Admin ================= */

    public function enqueue_admin_assets($hook) {
        // Produkt-Editor
        if (!in_array($hook, ['post.php','post-new.php'], true)) return;
        $screen = function_exists('get_current_screen') ? get_current_screen() : null;
        if (!$screen || $screen->post_type !== 'product') return;

        // WooCommerce Admin Assets
        if (wp_script_is('selectWoo', 'registered')) {
            wp_enqueue_script('selectWoo');
        }
        wp_enqueue_script('wc-enhanced-select');
        wp_enqueue_style('woocommerce_admin_styles');

        // Mini-Init (SelectWoo/Select2)
        $inline_js = "
            jQuery(function($){
                function init($el){
                    if (!$el || !$el.length) return;
                    var args = {
                        allowClear: true,
                        placeholder: $el.data('placeholder') || '".esc_js(__('Produkt suchen…', 'cdh-ra'))."',
                        minimumInputLength: 1,
                        ajax: {
                            url:  '".esc_url_raw(admin_url('admin-ajax.php'))."',
                            dataType: 'json',
                            delay: 200,
                            data: function(params){ return { term: params.term, action: 'cdh_ra_search' }; },
                            processResults: function(data){ return { results: data }; },
                            cache: true
                        }
                    };
                    if ($.fn.selectWoo) $el.selectWoo(args); else if ($.fn.select2) $el.select2(args);
                }
                $('.wc-product-search').each(function(){ init($(this)); });
            });
        ";
        wp_register_script('cdh-ra-admin-24', '', ['jquery','wc-enhanced-select'], '2.4.0', true);
        wp_enqueue_script('cdh-ra-admin-24');
        wp_add_inline_script('cdh-ra-admin-24', $inline_js);
    }

    public function add_metaboxes() {
        // 2.7: vier Plätze (z. B. Stick, Druck, Transfer). Bis 2.6 waren es zwei —
        // Einträge C/D gingen beim Speichern verloren.
        for ($i = 0; $i < self::SLOTS; $i++) {
            $buchstabe = chr(ord('A') + $i);
            add_meta_box('cdh_ra_slot_' . strtolower($buchstabe), sprintf(__('Pflicht-Zubehör %s', 'cdh-ra'), $buchstabe),
                function($post) use ($i) { $this->render_metabox_slot($post, $i); }, 'product', 'side', 'default');
        }
        add_meta_box('cdh_ra_staffel', __('Staffelpreise für CDH (Zubehör)', 'cdh-ra'), [$this, 'render_staffel'], 'product', 'normal', 'default');
    }

    /* ================= Staffelpreise (ab 2.6) ================= */

    private static function zahl($x) {
        $x = trim(str_replace(',', '.', (string) $x));
        return ($x === '' || !is_numeric($x)) ? null : round((float) $x, 4);
    }

    public function render_staffel($post) {
        wp_nonce_field('cdh_ra_staffel', 'cdh_ra_staffel_nonce');
        $rows = get_post_meta($post->ID, self::STAFFEL_KEY, true);
        if (!is_array($rows)) $rows = [];
        $rows = array_values($rows);
        // 2.6.1: mindestens so viele Zeilen wie Standardstufen; eine leere
        // Staffel mit den Standardmengen vorbelegen (ohne Preis = zählt nicht)
        if (!$rows) {
            foreach (self::STAFFEL_STANDARD as $ab) $rows[] = ['ab' => $ab, 'vk' => '', 'ek' => ''];
        }
        $anzahl = max(count($rows) + 2, count(self::STAFFEL_STANDARD));
        echo '<p>'.esc_html__('Nur für Zubehör-Artikel (z. B. Stick). Maßgeblich ist die Menge dieses Zubehörs im CDH-Auftrag – bei Sammel-Shops alle Bestellungen eines Lieferorts zusammen. Leeres EK: EK aus „Länge“. Zeilen ohne VK zählen nicht. Ohne Staffel gelten Länge (EK) und Breite (VK). Dieselben Werte lassen sich im TEXMA-Tool pflegen.', 'cdh-ra').'</p>';
        echo '<table class="widefat striped" style="max-width:520px"><thead><tr><th>'.esc_html__('ab Menge', 'cdh-ra').'</th><th>'.esc_html__('VK', 'cdh-ra').'</th><th>'.esc_html__('EK', 'cdh-ra').'</th></tr></thead><tbody>';
        for ($i = 0; $i < min($anzahl, self::STAFFEL_MAX); $i++) {
            $r = $rows[$i] ?? ['ab' => '', 'vk' => '', 'ek' => ''];
            $fmt = function($v) { return ($v === null || $v === '') ? '' : str_replace('.', ',', (string) $v); };
            printf('<tr><td><input type="number" min="1" step="1" name="cdh_ra_staffel[%1$d][ab]" value="%2$s" style="width:90px"></td>'
                 . '<td><input type="text" inputmode="decimal" name="cdh_ra_staffel[%1$d][vk]" value="%3$s" style="width:110px"></td>'
                 . '<td><input type="text" inputmode="decimal" name="cdh_ra_staffel[%1$d][ek]" value="%4$s" style="width:110px"></td></tr>',
                $i, esc_attr($r['ab'] ?? ''), esc_attr($fmt($r['vk'] ?? '')), esc_attr($fmt($r['ek'] ?? '')));
        }
        echo '</tbody></table>';
    }

    public function save_staffel($post_id) {
        if (!isset($_POST['cdh_ra_staffel_nonce']) || !wp_verify_nonce($_POST['cdh_ra_staffel_nonce'], 'cdh_ra_staffel')) return;
        if (!current_user_can('manage_options')) return;
        if (defined('DOING_AUTOSAVE') && DOING_AUTOSAVE) return;

        $staffel = [];
        foreach ((array) ($_POST['cdh_ra_staffel'] ?? []) as $row) {
            $row = (array) $row;
            $ab = isset($row['ab']) ? intval($row['ab']) : 0;
            $vk = self::zahl($row['vk'] ?? '');
            $ek = self::zahl($row['ek'] ?? '');
            if ($ab < 1 || $vk === null || $vk < 0) continue;      // unvollständige Zeile
            if ($ek !== null && ($ek < 0 || $ek > $vk)) $ek = null; // wie im Tool: EK ≤ VK
            $staffel[$ab] = ['ab' => $ab, 'vk' => $vk, 'ek' => $ek];
        }
        ksort($staffel);
        $staffel = array_slice(array_values($staffel), 0, self::STAFFEL_MAX);
        if ($staffel) update_post_meta($post_id, self::STAFFEL_KEY, $staffel);
        else delete_post_meta($post_id, self::STAFFEL_KEY);
    }

    private function render_metabox_slot($post, $slot = 0) {
        wp_nonce_field('cdh_ra_save', 'cdh_ra_nonce');
        $rows = get_post_meta($post->ID, self::META_KEY, true);
        if (!is_array($rows)) $rows = [];
        $row = isset($rows[$slot]) ? $rows[$slot] : ['accessory_id'=>0, 'qty_per_unit'=>1];
        $acc_id = intval($row['accessory_id'] ?? 0);
        $qty    = floatval($row['qty_per_unit'] ?? 1);
        $label  = $acc_id ? get_the_title($acc_id) : '';

        echo '<p>'.esc_html__('Zubehör (einfaches, veröffentlichtes Produkt). Menge = Bedarf pro 1 Stück Hauptprodukt.', 'cdh-ra').'</p>';
        echo '<label><strong>'.esc_html__('Zubehör', 'cdh-ra').'</strong></label>';
        printf(
            '<select class="wc-product-search" style="width:100%%;" name="cdh_ra_slot%1$d[accessory_id]" data-placeholder="%2$s">%3$s</select>',
            $slot+1,
            esc_attr__('Produkt suchen…', 'cdh-ra'),
            $acc_id ? '<option value="'.esc_attr($acc_id).'" selected>'.esc_html($label).'</option>' : ''
        );
        echo '<label style="display:block;margin-top:8px;"><strong>'.esc_html__('Menge pro 1 Stück Hauptprodukt', 'cdh-ra').'</strong></label>';
        echo '<input type="number" step="0.0001" min="0" name="cdh_ra_slot'.($slot+1).'[qty_per_unit]" value="'.esc_attr($qty).'" style="width:100%;">';
        echo '<p><em>'.esc_html__('Nur Admin darf ändern. Pflicht-Zubehör ist für Kunden nicht entfernbar.', 'cdh-ra').'</em></p>';
    }

    public function save_metaboxes($post_id) {
        if (!isset($_POST['cdh_ra_nonce']) || !wp_verify_nonce($_POST['cdh_ra_nonce'], 'cdh_ra_save')) return;
        if (!current_user_can('manage_options')) return;
        if (defined('DOING_AUTOSAVE') && DOING_AUTOSAVE) return;

        $slots = [];
        for ($i=1; $i<=self::SLOTS; $i++) {
            $row = isset($_POST['cdh_ra_slot'.$i]) ? (array) $_POST['cdh_ra_slot'.$i] : [];
            $acc = isset($row['accessory_id']) ? intval($row['accessory_id']) : 0;
            $qty = isset($row['qty_per_unit']) ? floatval($row['qty_per_unit']) : 0;
            if ($acc > 0 && $qty > 0 && $acc !== $post_id) {
                $p = wc_get_product($acc);
                if ($p && $p->is_type('simple') && 'publish' === get_post_status($acc)) {
                    $slots[] = ['accessory_id'=>$acc, 'qty_per_unit'=>round($qty,4)];
                }
            }
        }
        update_post_meta($post_id, self::META_KEY, $slots);
    }

    /** AJAX: simple + published, search name or SKU */
    public function ajax_search_products() {
        if (!current_user_can('manage_options')) wp_send_json([]);
        $term = isset($_GET['term']) ? wc_clean(wp_unslash($_GET['term'])) : '';

        // Title search
        $ids = wc_get_products([
            'status' => 'publish',
            'limit'  => 25,
            'type'   => ['simple'],
            'return' => 'ids',
            'search' => $term,
        ]);

        // SKU search
        if ($term) {
            $sku_query = new WP_Query([
                'post_type'      => 'product',
                'post_status'    => 'publish',
                'posts_per_page' => 25,
                'fields'         => 'ids',
                'meta_query'     => [[
                    'key'     => '_sku',
                    'value'   => $term,
                    'compare' => 'LIKE',
                ]],
                'tax_query'      => [[
                    'taxonomy' => 'product_type',
                    'field'    => 'slug',
                    'terms'    => ['simple'],
                ]],
            ]);
            $ids = array_unique(array_merge($ids, $sku_query->posts));
        }

        $out = [];
        foreach ($ids as $pid) {
            $p = wc_get_product($pid);
            if (!$p || !$p->is_type('simple') || 'publish' !== get_post_status($pid)) continue;
            $sku = $p->get_sku();
            $text = $p->get_name() . ($sku ? ' — SKU: '.$sku : '') . ' (ID '.$pid.')';
            $out[] = ['id'=>$pid, 'text'=>$text];
        }
        wp_send_json($out);
    }

    /* ================= Cart Core ================= */

    /** Zubehör samt dessen eigenem Zubehör (ab 2.7): Druck → Transfer. Mengen
     *  werden multipliziert; höchstens TIEFE Ebenen, Kreisläufe werden übersprungen.
     *  Gleiche Rechnung wie im TEXMA-Import (woo_to_cdh.zubehoer_ergaenzen). */
    private function add_required(array &$map, int $acc_id, float $qty, int $tiefe, array $pfad) {
        if (!isset($map[$acc_id])) $map[$acc_id] = 0.0;
        $map[$acc_id] += $qty;
        if ($tiefe >= self::TIEFE) return;
        $pfad[] = $acc_id;
        $unter = get_post_meta($acc_id, self::META_KEY, true);
        if (!is_array($unter)) return;
        foreach ($unter as $r) {
            $sub = intval($r['accessory_id'] ?? 0);
            $per = floatval($r['qty_per_unit'] ?? 0);
            if ($sub <= 0 || $per <= 0 || in_array($sub, $pfad, true)) continue;
            $this->add_required($map, $sub, $qty * $per, $tiefe + 1, $pfad);
        }
    }

    private function get_required_map_for_cart() : array {
        $map = [];
        if (WC()->cart && !WC()->cart->is_empty()) {
            foreach (WC()->cart->get_cart() as $key => $item) {
                if (!empty($item[self::CART_FLAG])) continue;
                $product_id   = $item['product_id'];
                $variation_id = !empty($item['variation_id']) ? $item['variation_id'] : 0;

                // Regeln: Variante überschreibt Parent
                $rules = get_post_meta($product_id, self::META_KEY, true);
                if (!is_array($rules)) $rules = [];
                if ($variation_id) {
                    $r_var = get_post_meta($variation_id, self::META_KEY, true);
                    if (is_array($r_var) && !empty($r_var)) $rules = $r_var;
                }

                if (!empty($rules)) {
                    $qty_main = isset($item['quantity']) ? floatval($item['quantity']) : 1;
                    foreach ($rules as $r) {
                        $acc_id = intval($r['accessory_id'] ?? 0);
                        $per    = floatval($r['qty_per_unit'] ?? 0);
                        if ($acc_id <= 0 || $per <= 0 || $acc_id === intval($product_id)) continue;
                        $this->add_required($map, $acc_id, $qty_main * $per, 1, [intval($product_id)]);
                    }
                }
            }
        }
        // Normalize ints for clean display
        foreach ($map as $acc_id => $q) {
            $q = round($q,4);
            if (abs($q - round($q)) < 0.0001) $q = (int) round($q);
            $map[$acc_id] = $q;
        }
        return $map;
    }

    public function sync_cart() {
        if (!WC()->cart) return;
        $required = $this->get_required_map_for_cart();

        // bereits vorhandene Zubehörzeilen merken
        $existing = [];
        foreach (WC()->cart->get_cart() as $key => $item) {
            if (!empty($item[self::CART_FLAG])) {
                $existing[intval($item['product_id'])] = $key;
            }
        }

        // hinzufügen/aktualisieren
        foreach ($required as $acc_id => $need_qty) {
            if ($need_qty <= 0) continue;
            $product = wc_get_product($acc_id);
            if (!$product || !$product->is_type('simple')) continue; // 0,00 € erlaubt

            if (isset($existing[$acc_id])) {
                $k = $existing[$acc_id];
                WC()->cart->cart_contents[$k]['quantity'] = $need_qty;
                WC()->cart->cart_contents[$k][self::CART_FLAG] = true;
            } else {
                $added_key = WC()->cart->add_to_cart($acc_id, $need_qty, 0, [], [ self::CART_FLAG => true ]);
                if ($added_key) $existing[$acc_id] = $added_key;
            }
        }

        // entfernen, wenn nicht mehr benötigt
        foreach ($existing as $acc_id => $key) {
            if (!isset($required[$acc_id]) || $required[$acc_id] <= 0) {
                WC()->cart->remove_cart_item($key);
            }
        }
    }

    public function order_accessories_last() {
        if (!WC()->cart || empty(WC()->cart->cart_contents)) return;
        $main = []; $acc = [];
        foreach (WC()->cart->cart_contents as $k => $item) {
            if (!empty($item[self::CART_FLAG])) $acc[$k] = $item; else $main[$k] = $item;
        }
        WC()->cart->cart_contents = array_merge($main, $acc);
    }

    public function hide_remove_for_accessories($link, $cart_item_key) {
        $item = WC()->cart->get_cart_item($cart_item_key);
        if (!empty($item[self::CART_FLAG])) return '';
        return $link;
    }

    public function lock_quantity_for_accessories($product_quantity, $cart_item_key, $cart_item) {
        if (!empty($cart_item[self::CART_FLAG])) return wc_clean($cart_item['quantity']);
        return $product_quantity;
    }

    public function block_qty_change_on_update($passed, $cart_item_key, $values, $quantity) {
        if (!empty($values[self::CART_FLAG])) {
            // Menge zurücksetzen und Hinweis
            $current = WC()->cart->cart_contents[$cart_item_key]['quantity'] ?? $quantity;
            WC()->cart->cart_contents[$cart_item_key]['quantity'] = $current;
            wc_add_notice(__('Die Menge des automatisch hinzugefügten Zubehörs kann nicht geändert werden.', 'cdh-ra'), 'notice');
            return false;
        }
        return $passed;
    }

    public function label_cart_item_name($name, $cart_item, $cart_item_key) {
        if (!empty($cart_item[self::CART_FLAG])) {
            $name .= ' <small style="opacity:.7;">(' . esc_html__('automatisch hinzugefügt', 'cdh-ra') . ')</small>';
        }
        return $name;
    }

    public function prevent_direct_add_of_accessory($passed, $product_id, $quantity) {
        // Kein spezieller Block – Mengen & Preise kommen vom Zubehörprodukt (0,00 € erlaubt)
        return $passed;
    }
}

add_action('plugins_loaded', function() {
    if (class_exists('WooCommerce')) {
        new CDH_Required_Accessories_24();
    }
});
