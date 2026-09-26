<?php
/**
 * Plugin Name: CDH Required Accessories
 * Description: Pflicht-Zubehör pro Produkt (Aggregation je SKU, nicht entfernbar, Zubehör am Warenkorb-Ende). Stand 2.4 + 2. feste Metaboxen (A & B).
 * Author: TEXMA
 * Version: 2.4.0
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

    public function __construct() {
        // Admin UI (2 feste Slots)
        add_action('add_meta_boxes',           [$this, 'add_metaboxes']);
        add_action('save_post_product',        [$this, 'save_metaboxes']);
        add_action('admin_enqueue_scripts',    [$this, 'enqueue_admin_assets']);
        add_action('wp_ajax_cdh_ra_search',    [$this, 'ajax_search_products']); // Produktsuche

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
        add_meta_box('cdh_ra_slot_a', __('Pflicht-Zubehör A', 'cdh-ra'), function($post){ $this->render_metabox_slot($post, 0); }, 'product', 'side', 'default');
        add_meta_box('cdh_ra_slot_b', __('Pflicht-Zubehör B', 'cdh-ra'), function($post){ $this->render_metabox_slot($post, 1); }, 'product', 'side', 'default');
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
        for ($i=1; $i<=2; $i++) {
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
                        if ($acc_id <= 0 || $per <= 0) continue;
                        if (!isset($map[$acc_id])) $map[$acc_id] = 0.0;
                        $map[$acc_id] += $qty_main * $per;
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
