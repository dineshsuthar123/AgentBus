package fixture;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class OrderController {
    private final OrderService service = new OrderService();

    @GetMapping("/orders/total")
    public int total() {
        return service.total(2, 5);
    }
}
